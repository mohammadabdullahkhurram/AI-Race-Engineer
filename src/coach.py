"""
coach.py
Rule-based coaching engine. Reads lap analysis JSON and generates
corner-by-corner coaching feedback using deterministic racing rules.
No API key required — runs fully offline.
"""

import json
import sys
from pathlib import Path


# ── Thresholds ────────────────────────────────────────────────────────────────

BRAKE_POINT_EARLY_THRESHOLD_M  = 5    # metres earlier than ref = "braking too early"
BRAKE_POINT_LATE_THRESHOLD_M   = 5    # metres later than ref  = "braking too late"
APEX_SPEED_LOSS_HIGH_KMH       = 5    # km/h below ref = major issue
APEX_SPEED_LOSS_MED_KMH        = 2    # km/h below ref = moderate issue
THROTTLE_LATE_THRESHOLD_M      = 10   # metres later pick-up = coaching point
THROTTLE_EARLY_THRESHOLD_M     = 10   # metres earlier = worth noting
ENTRY_SPEED_LOSS_KMH           = 3    # km/h below ref entry = entry issue
SECTOR_TIME_SIGNIFICANT_S      = 0.05 # 50ms = worth mentioning per sector
TIME_GAIN_ESTIMATE_PER_KMH     = 0.03 # rough s per km/h of apex speed recovered


# ── Sector feedback rules ─────────────────────────────────────────────────────

def _sector_feedback(sector: dict) -> dict:
    dt = sector["time_delta_s"]
    speed_delta = sector["speed_delta_at_min_kmh"]
    throttle_delta = sector["comp_avg_throttle"] - sector["ref_avg_throttle"]
    brake_delta = sector["comp_avg_brake"] - sector["ref_avg_brake"]

    issues = []
    positives = []

    if speed_delta < -APEX_SPEED_LOSS_HIGH_KMH:
        issues.append(
            f"Minimum corner speed is {abs(speed_delta):.1f} km/h below reference — "
            f"driver is over-slowing significantly through this sector."
        )
    elif speed_delta < -APEX_SPEED_LOSS_MED_KMH:
        issues.append(
            f"Carrying {abs(speed_delta):.1f} km/h less than reference through corners — "
            f"small gains available at each apex."
        )
    elif speed_delta > 2:
        positives.append(
            f"Corner speed is {speed_delta:.1f} km/h above reference — "
            f"good commitment through the bends."
        )

    if throttle_delta < -0.05:
        issues.append(
            f"Average throttle application is {abs(throttle_delta)*100:.0f}% lower than reference — "
            f"driver is hesitant getting back on power."
        )
    elif throttle_delta > 0.05:
        positives.append(
            f"Throttle commitment is strong — {throttle_delta*100:.0f}% higher average than reference."
        )

    if brake_delta > 0.05:
        issues.append(
            f"Over-braking vs reference — brake usage is {brake_delta*100:.0f}% higher on average. "
            f"Driver may be leaving brakes on too long into corners."
        )

    if not issues and dt < -SECTOR_TIME_SIGNIFICANT_S:
        positives.append("Sector is clean — no major issues detected.")

    if not issues:
        headline = f"Solid sector — {abs(dt):.3f}s {'ahead of' if dt < 0 else 'off'} reference."
    else:
        headline = issues[0]

    detail = " ".join(issues[1:]) if len(issues) > 1 else (positives[0] if positives else "")

    return {
        "sector": sector["sector_name"],
        "time_delta_s": dt,
        "headline": headline,
        "details": detail,
        "has_issues": len(issues) > 0,
    }


# ── Corner feedback rules ─────────────────────────────────────────────────────

def _corner_feedback(corner: dict) -> dict | None:
    issues = []

    apex_delta     = corner["apex_speed_delta_kmh"]
    entry_delta    = corner["entry_speed_delta_kmh"]
    brake_delta    = corner.get("brake_point_delta_m")
    throttle_delta = corner.get("throttle_pickup_delta_m")

    # ── Braking point ─────────────────────────────────────────────────────────
    if brake_delta is not None:
        if brake_delta < -BRAKE_POINT_EARLY_THRESHOLD_M:
            issues.append({
                "issue": (
                    f"Braking {abs(brake_delta):.0f}m too early compared to reference."
                ),
                "fix": (
                    f"Move the brake marker {abs(brake_delta):.0f}m later. "
                    f"Reference brakes at {corner['ref_brake_point_m']:.0f}m, "
                    f"driver at {corner['comp_brake_point_m']:.0f}m."
                ),
                "gain": abs(brake_delta) * 0.005,
                "evidence": (
                    f"Brake point: {corner['comp_brake_point_m']:.0f}m vs "
                    f"{corner['ref_brake_point_m']:.0f}m reference ({brake_delta:.0f}m delta)."
                ),
            })
        elif brake_delta > BRAKE_POINT_LATE_THRESHOLD_M:
            issues.append({
                "issue": (
                    f"Braking {brake_delta:.0f}m later than reference — risk of running wide."
                ),
                "fix": (
                    f"Bring the brake point back by {brake_delta:.0f}m for consistency. "
                    f"Reference uses {corner['ref_brake_point_m']:.0f}m."
                ),
                "gain": 0.0,
                "evidence": (
                    f"Brake point: {corner['comp_brake_point_m']:.0f}m vs "
                    f"{corner['ref_brake_point_m']:.0f}m reference."
                ),
            })

    # ── Apex speed ────────────────────────────────────────────────────────────
    if apex_delta < -APEX_SPEED_LOSS_HIGH_KMH:
        gain = abs(apex_delta) * TIME_GAIN_ESTIMATE_PER_KMH
        issues.append({
            "issue": (
                f"Apex speed is {abs(apex_delta):.1f} km/h below reference "
                f"({corner['comp_apex_speed_kmh']} vs {corner['ref_apex_speed_kmh']} km/h). "
                f"Significant corner exit speed deficit."
            ),
            "fix": _apex_fix(corner, apex_delta, brake_delta),
            "gain": gain,
            "evidence": (
                f"Apex: {corner['comp_apex_speed_kmh']} km/h vs "
                f"{corner['ref_apex_speed_kmh']} km/h reference ({apex_delta:.1f} km/h)."
            ),
        })
    elif apex_delta < -APEX_SPEED_LOSS_MED_KMH:
        gain = abs(apex_delta) * TIME_GAIN_ESTIMATE_PER_KMH
        issues.append({
            "issue": (
                f"Losing {abs(apex_delta):.1f} km/h at the apex "
                f"({corner['comp_apex_speed_kmh']} vs {corner['ref_apex_speed_kmh']} km/h)."
            ),
            "fix": _apex_fix(corner, apex_delta, brake_delta),
            "gain": gain,
            "evidence": (
                f"Apex: {corner['comp_apex_speed_kmh']} km/h vs "
                f"{corner['ref_apex_speed_kmh']} km/h."
            ),
        })

    # ── Entry speed ───────────────────────────────────────────────────────────
    if entry_delta < -ENTRY_SPEED_LOSS_KMH and apex_delta >= -APEX_SPEED_LOSS_MED_KMH:
        issues.append({
            "issue": (
                f"Entry speed is {abs(entry_delta):.1f} km/h below reference "
                f"but apex speed is acceptable — over-braking on entry."
            ),
            "fix": (
                "Trail the brakes into the corner rather than releasing fully at turn-in. "
                "Carry more entry speed and use trail braking to rotate the car to the apex."
            ),
            "gain": abs(entry_delta) * 0.01,
            "evidence": (
                f"Entry: {corner['comp_entry_speed_kmh']} km/h vs "
                f"{corner['ref_entry_speed_kmh']} km/h reference."
            ),
        })

    # ── Throttle pick-up ──────────────────────────────────────────────────────
    if throttle_delta is not None and throttle_delta > THROTTLE_LATE_THRESHOLD_M:
        gain = throttle_delta * 0.004
        issues.append({
            "issue": (
                f"Throttle pick-up is {throttle_delta:.0f}m later than reference after the apex."
            ),
            "fix": (
                f"Get back to full throttle {throttle_delta:.0f}m earlier. "
                f"Reference applies throttle at {corner['ref_throttle_pickup_m']:.0f}m, "
                f"driver at {corner['comp_throttle_pickup_m']:.0f}m. "
                f"Trust the apex and commit — late throttle bleeds straight-line speed."
            ),
            "gain": gain,
            "evidence": (
                f"Throttle pickup: {corner['comp_throttle_pickup_m']:.0f}m vs "
                f"{corner['ref_throttle_pickup_m']:.0f}m reference."
            ),
        })

    if not issues:
        return None

    primary = max(issues, key=lambda x: x["gain"])

    return {
        "corner": corner["corner_name"],
        "corner_type": corner["corner_type"],
        "dist_m": corner["dist_m"],
        "time_delta_s": corner["time_delta_s"],
        "technique_issue": primary["issue"],
        "fix": primary["fix"],
        "data_evidence": primary["evidence"],
        "time_gain_s": round(primary["gain"], 3),
        "all_issues": issues,
    }


def _apex_fix(corner: dict, apex_delta: float, brake_delta) -> str:
    target = f"{corner['ref_apex_speed_kmh']:.0f} km/h"
    if brake_delta is not None and brake_delta < -10:
        return (
            f"You are braking {abs(brake_delta):.0f}m too early and shedding too much speed. "
            f"Delay the brake point and commit to a later apex. "
            f"Target {target} through here — it is completely achievable."
        )
    elif brake_delta is not None and brake_delta > 5:
        return (
            f"Despite a late brake point, apex speed is below reference — car not rotating. "
            f"Use higher initial brake pressure to pitch the car in, trail off through apex. "
            f"Hit {target} and the exit speed takes care of itself."
        )
    else:
        return (
            f"Carry more speed to the apex — target {target}. "
            f"Take a wider entry to enable a later apex and trust the exit. "
            f"This corner is where straight-line speed is won or lost."
        )


# ── Overall summary ───────────────────────────────────────────────────────────

def _motivational_opener(delta: float) -> str:
    if delta <= 0:
        return "Excellent lap — you are ahead of the reference."
    elif delta < 1.0:
        return "Strong lap. The gap to reference is tight and very closeable."
    elif delta < 3.0:
        return "Good foundations here. The time is there — it just needs unlocking corner by corner."
    elif delta < 6.0:
        return "Plenty of time available. Focus on the priority corners and the gap will close fast."
    else:
        return "Big gap to reference, but the data shows exactly where it is hiding. Attack these points."


def _motivational_closer(total_gain: float) -> str:
    if total_gain <= 0:
        return "Keep pushing — consistency is the foundation of fast laps."
    elif total_gain < 0.5:
        return f"Fix these points and you gain back {total_gain:.2f}s. Small margins, big results."
    elif total_gain < 1.5:
        return f"These corrections alone are worth {total_gain:.2f}s. That is a significant step forward."
    else:
        return f"Executing these fixes could recover {total_gain:.2f}s — a completely different lap time."


def _overall_summary(analysis: dict, corner_issues: list) -> str:
    delta = analysis["total_time_delta_s"]
    sign = "slower" if delta > 0 else "faster"
    gap_str = f"{abs(delta):.3f}s {sign} than reference"

    worst_sector = max(
        analysis["sectors"], key=lambda s: s["time_delta_s"], default=None
    )
    total_gain = sum(c["time_gain_s"] for c in corner_issues)

    parts = [_motivational_opener(delta)]
    parts.append(f"Current gap: {gap_str}.")

    if worst_sector and worst_sector["time_delta_s"] > SECTOR_TIME_SIGNIFICANT_S:
        spd = abs(worst_sector["speed_delta_at_min_kmh"])
        dt  = worst_sector["time_delta_s"]
        sec = worst_sector["sector_name"]
        parts.append(
            f"The biggest opportunity is {sec} ({dt:+.3f}s), "
            f"where {spd:.1f} km/h is being left on the table through the corners."
        )

    parts.append(_motivational_closer(total_gain))
    return " ".join(parts)


# ── Priority actions ──────────────────────────────────────────────────────────

def _build_priority_actions(corner_issues: list) -> list:
    actions = []
    for c in corner_issues:
        for issue in c["all_issues"]:
            if issue["gain"] > 0.02:
                actions.append({
                    "location": c["corner"],
                    "issue": issue["issue"],
                    "instruction": issue["fix"],
                    "time_gain_s": round(issue["gain"], 3),
                    "evidence": issue["evidence"],
                })

    actions.sort(key=lambda x: x["time_gain_s"], reverse=True)
    for i, a in enumerate(actions):
        a["priority"] = i + 1
        a["confidence"] = "high" if a["time_gain_s"] > 0.1 else "medium"

    return actions[:6]


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_coaching_report(analysis: dict) -> dict:
    sector_feedback  = [_sector_feedback(s) for s in analysis.get("sectors", [])]
    corner_raw       = [_corner_feedback(c) for c in analysis.get("corners", [])]
    corner_feedback  = sorted(
        [c for c in corner_raw if c is not None],
        key=lambda c: c["time_gain_s"],
        reverse=True,
    )
    priority_actions = _build_priority_actions(corner_feedback)
    summary          = _overall_summary(analysis, corner_feedback)

    positives = []
    for sec in sector_feedback:
        if not sec["has_issues"] or sec["time_delta_s"] < -0.05:
            positives.append(
                f"{sec['sector']}: {abs(sec['time_delta_s']):.3f}s ahead of reference."
            )
    for c in analysis.get("corners", []):
        if c["apex_speed_delta_kmh"] > 2:
            positives.append(
                f"{c['corner_name']}: carrying {c['apex_speed_delta_kmh']:.1f} km/h "
                f"more than reference at the apex."
            )

    return {
        "overall_summary": summary,
        "priority_actions": priority_actions,
        "sector_feedback": sector_feedback,
        "corner_coaching": corner_feedback,
        "positive_observations": positives[:3],
        "telemetry_summary": {
            "ref_lap_time_s": analysis["ref_lap_time_s"],
            "comp_lap_time_s": analysis["comp_lap_time_s"],
            "total_delta_s": analysis["total_time_delta_s"],
            "sectors": analysis["sectors"],
            "corners": analysis["corners"],
        },
    }


def print_coaching_report(coaching: dict):
    tel   = coaching.get("telemetry_summary", {})
    delta = tel.get("total_delta_s", 0)
    sign  = "+" if delta > 0 else ""

    print("\n" + "=" * 60)
    print("RACE COACHING REPORT")
    print("=" * 60)
    print(
        f"Lap time : {tel.get('comp_lap_time_s', '?')}s  |  "
        f"Reference: {tel.get('ref_lap_time_s', '?')}s  |  "
        f"Gap: {sign}{delta:.3f}s"
    )
    print()

    print("SUMMARY")
    print("-" * 40)
    print(coaching.get("overall_summary", ""))
    print()

    print("PRIORITY ACTIONS  (biggest gains first)")
    print("-" * 40)
    for a in coaching.get("priority_actions", []):
        print(f"#{a['priority']} {a['location']}  |  ~{a['time_gain_s']:.3f}s  |  {a['confidence']} confidence")
        print(f"  Problem:  {a['issue']}")
        print(f"  Fix:      {a['instruction']}")
        print(f"  Evidence: {a['evidence']}")
        print()

    print("SECTOR FEEDBACK")
    print("-" * 40)
    for sec in coaching.get("sector_feedback", []):
        dt   = sec.get("time_delta_s", 0)
        sign = "+" if dt > 0 else ""
        print(f"{sec['sector']} ({sign}{dt:.3f}s):  {sec['headline']}")
        if sec.get("details"):
            print(f"  {sec['details']}")
        print()

    print("CORNER COACHING")
    print("-" * 40)
    for c in coaching.get("corner_coaching", []):
        print(f"{c['corner']} @ {c['dist_m']:.0f}m  |  ~{c['time_gain_s']:.3f}s gain")
        print(f"  Issue:    {c['technique_issue']}")
        print(f"  Fix:      {c['fix']}")
        print(f"  Evidence: {c['data_evidence']}")
        print()

    if coaching.get("positive_observations"):
        print("WHAT'S WORKING")
        print("-" * 40)
        for obs in coaching["positive_observations"]:
            print(f"  + {obs}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python coach.py <analysis.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        analysis = json.load(f)

    report = generate_coaching_report(analysis)
    print_coaching_report(report)

    out_path = sys.argv[1].replace(".json", "_coaching.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Full report saved → {out_path}")
