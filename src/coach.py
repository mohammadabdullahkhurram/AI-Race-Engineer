import json
import sys
from pathlib import Path

BRAKE_POINT_EARLY_THRESHOLD_M = 5
BRAKE_POINT_LATE_THRESHOLD_M = 5
APEX_SPEED_LOSS_HIGH_KMH = 5
APEX_SPEED_LOSS_MED_KMH = 2
THROTTLE_LATE_THRESHOLD_M = 10
ENTRY_SPEED_LOSS_KMH = 3
SECTOR_TIME_SIGNIFICANT_S = 0.05
TIME_GAIN_ESTIMATE_PER_KMH = 0.03

def _sector_feedback(sector):
    dt = sector["time_delta_s"]
    speed_delta = sector["speed_delta_at_min_kmh"]
    throttle_delta = sector["comp_avg_throttle"] - sector["ref_avg_throttle"]
    brake_delta = sector["comp_avg_brake"] - sector["ref_avg_brake"]
    issues = []
    positives = []
    if speed_delta < -APEX_SPEED_LOSS_HIGH_KMH:
        issues.append(f"Minimum corner speed is {abs(speed_delta):.1f} km/h below reference — driver is over-slowing significantly.")
    elif speed_delta < -APEX_SPEED_LOSS_MED_KMH:
        issues.append(f"Carrying {abs(speed_delta):.1f} km/h less than reference through corners.")
    elif speed_delta > 2:
        positives.append(f"Corner speed is {speed_delta:.1f} km/h above reference — good commitment.")
    if throttle_delta < -0.05:
        issues.append(f"Average throttle is {abs(throttle_delta)*100:.0f}% lower than reference — hesitant on power.")
    if brake_delta > 0.05:
        issues.append(f"Over-braking vs reference — brake trace is heavier than reference. Driver may be leaving brakes on too long into corners.")
    headline = issues[0] if issues else f"Solid sector — {abs(dt):.3f}s {'ahead of' if dt < 0 else 'off'} reference."
    detail = " ".join(issues[1:]) if len(issues) > 1 else (positives[0] if positives else "")
    return {"sector": sector["sector_name"], "time_delta_s": dt, "headline": headline, "details": detail, "has_issues": len(issues) > 0}

def _apex_fix(corner, apex_delta, brake_delta):
    if brake_delta is not None and brake_delta < -10:
        return f"Braking too early and shedding too much speed. Delay brake point and commit to a later apex. Reference carries {corner['ref_apex_speed_kmh']:.0f} km/h here."
    elif brake_delta is not None and brake_delta > 5:
        return f"Despite late braking, apex speed is low — car not rotating. Use higher initial brake pressure to pitch the car in, trail off through apex to hit {corner['ref_apex_speed_kmh']:.0f} km/h."
    else:
        return f"Carry more speed through apex — target {corner['ref_apex_speed_kmh']:.0f} km/h. Widen entry for a later apex and maximise exit speed."

def _corner_feedback(corner):
    issues = []
    apex_delta = corner["apex_speed_delta_kmh"]
    entry_delta = corner["entry_speed_delta_kmh"]
    brake_delta = corner.get("brake_point_delta_m")
    throttle_delta = corner.get("throttle_pickup_delta_m")
    if brake_delta is not None:
        if brake_delta < -BRAKE_POINT_EARLY_THRESHOLD_M:
            issues.append({"issue": f"Braking {abs(brake_delta):.0f}m too early.", "fix": f"Move brake point {abs(brake_delta):.0f}m later. Reference at {corner['ref_brake_point_m']:.0f}m, driver at {corner['comp_brake_point_m']:.0f}m.", "gain": abs(brake_delta)*0.005, "evidence": f"Brake point: {corner['comp_brake_point_m']:.0f}m vs {corner['ref_brake_point_m']:.0f}m ({brake_delta:.0f}m delta)."})
        elif brake_delta > BRAKE_POINT_LATE_THRESHOLD_M:
            issues.append({"issue": f"Braking {brake_delta:.0f}m later than reference — risk of running wide.", "fix": f"Move brake point back {brake_delta:.0f}m. Reference uses {corner['ref_brake_point_m']:.0f}m.", "gain": 0.0, "evidence": f"Brake point: {corner['comp_brake_point_m']:.0f}m vs {corner['ref_brake_point_m']:.0f}m."})
    if apex_delta < -APEX_SPEED_LOSS_HIGH_KMH:
        gain = abs(apex_delta) * TIME_GAIN_ESTIMATE_PER_KMH
        issues.append({"issue": f"Apex speed {abs(apex_delta):.1f} km/h below reference ({corner['comp_apex_speed_kmh']} vs {corner['ref_apex_speed_kmh']} km/h).", "fix": _apex_fix(corner, apex_delta, brake_delta), "gain": gain, "evidence": f"Apex: {corner['comp_apex_speed_kmh']} km/h vs {corner['ref_apex_speed_kmh']} km/h ({apex_delta:.1f} km/h)."})
    elif apex_delta < -APEX_SPEED_LOSS_MED_KMH:
        gain = abs(apex_delta) * TIME_GAIN_ESTIMATE_PER_KMH
        issues.append({"issue": f"Losing {abs(apex_delta):.1f} km/h at apex ({corner['comp_apex_speed_kmh']} vs {corner['ref_apex_speed_kmh']} km/h).", "fix": _apex_fix(corner, apex_delta, brake_delta), "gain": gain, "evidence": f"Apex: {corner['comp_apex_speed_kmh']} vs {corner['ref_apex_speed_kmh']} km/h."})
    if entry_delta < -ENTRY_SPEED_LOSS_KMH and apex_delta >= -APEX_SPEED_LOSS_MED_KMH:
        issues.append({"issue": f"Entry speed {abs(entry_delta):.1f} km/h below reference but apex OK — over-braking on entry.", "fix": "Trail brakes into corner instead of releasing at turn-in. Carry more entry speed and rotate to apex.", "gain": abs(entry_delta)*0.01, "evidence": f"Entry: {corner['comp_entry_speed_kmh']} vs {corner['ref_entry_speed_kmh']} km/h."})
    if throttle_delta is not None and throttle_delta > THROTTLE_LATE_THRESHOLD_M:
        gain = throttle_delta * 0.004
        issues.append({"issue": f"Throttle pick-up {throttle_delta:.0f}m later than reference.", "fix": f"Apply throttle {throttle_delta:.0f}m earlier. Reference at {corner['ref_throttle_pickup_m']:.0f}m, driver at {corner['comp_throttle_pickup_m']:.0f}m. Late throttle bleeds straight-line speed.", "gain": gain, "evidence": f"Throttle pickup: {corner['comp_throttle_pickup_m']:.0f}m vs {corner['ref_throttle_pickup_m']:.0f}m."})
    if not issues:
        return None
    primary = max(issues, key=lambda x: x["gain"])
    return {"corner": corner["corner_name"], "corner_type": corner["corner_type"], "dist_m": corner["dist_m"], "time_delta_s": corner["time_delta_s"], "technique_issue": primary["issue"], "fix": primary["fix"], "data_evidence": primary["evidence"], "time_gain_s": round(primary["gain"], 3), "all_issues": issues}

def _overall_summary(analysis, corner_issues):
    delta = analysis["total_time_delta_s"]
    gap_str = f"{abs(delta):.3f}s {'slower' if delta > 0 else 'faster'} than reference"
    worst_sector = max(analysis["sectors"], key=lambda s: s["time_delta_s"], default=None)
    total_gain = sum(c["time_gain_s"] for c in corner_issues)
    parts = [f"Driver is {gap_str}."]
    if worst_sector and worst_sector["time_delta_s"] > SECTOR_TIME_SIGNIFICANT_S:
        parts.append(f"Biggest loss is in {worst_sector['sector_name']} ({worst_sector['time_delta_s']:+.3f}s), driven by {abs(worst_sector['speed_delta_at_min_kmh']):.1f} km/h corner speed deficit.")
    if total_gain > 0.1:
        parts.append(f"Correcting identified issues could recover approximately {total_gain:.2f}s.")
    return " ".join(parts)

def _build_priority_actions(corner_issues):
    actions = []
    for c in corner_issues:
        for issue in c["all_issues"]:
            if issue["gain"] > 0.02:
                actions.append({"location": c["corner"], "issue": issue["issue"], "instruction": issue["fix"], "time_gain_s": round(issue["gain"], 3), "evidence": issue["evidence"]})
    actions.sort(key=lambda x: x["time_gain_s"], reverse=True)
    for i, a in enumerate(actions):
        a["priority"] = i + 1
        a["confidence"] = "high" if a["time_gain_s"] > 0.1 else "medium"
    return actions[:6]

def generate_coaching_report(analysis):
    sector_feedback = [_sector_feedback(s) for s in analysis.get("sectors", [])]
    corner_raw = [_corner_feedback(c) for c in analysis.get("corners", [])]
    corner_feedback = sorted([c for c in corner_raw if c is not None], key=lambda c: c["time_gain_s"], reverse=True)
    priority_actions = _build_priority_actions(corner_feedback)
    summary = _overall_summary(analysis, corner_feedback)
    positives = []
    for sec in sector_feedback:
        if not sec["has_issues"] or sec["time_delta_s"] < -0.05:
            positives.append(f"{sec['sector']}: {abs(sec['time_delta_s']):.3f}s ahead of reference.")
    for c in analysis.get("corners", []):
        if c["apex_speed_delta_kmh"] > 2:
            positives.append(f"{c['corner_name']}: carrying {c['apex_speed_delta_kmh']:.1f} km/h more than reference at apex.")
    return {"overall_summary": summary, "priority_actions": priority_actions, "sector_feedback": sector_feedback, "corner_coaching": corner_feedback, "positive_observations": positives[:3], "telemetry_summary": {"ref_lap_time_s": analysis["ref_lap_time_s"], "comp_lap_time_s": analysis["comp_lap_time_s"], "total_delta_s": analysis["total_time_delta_s"], "sectors": analysis["sectors"], "corners": analysis["corners"]}}

def print_coaching_report(coaching):
    tel = coaching.get("telemetry_summary", {})
    delta = tel.get("total_delta_s", 0)
    sign = "+" if delta > 0 else ""
    print("\n" + "="*60)
    print("RACE COACHING REPORT")
    print("="*60)
    print(f"Lap time : {tel.get('comp_lap_time_s','?')}s  |  Reference: {tel.get('ref_lap_time_s','?')}s  |  Gap: {sign}{delta:.3f}s")
    print()
    print("SUMMARY")
    print("-"*40)
    print(coaching.get("overall_summary",""))
    print()
    print("PRIORITY ACTIONS  (biggest gains first)")
    print("-"*40)
    for a in coaching.get("priority_actions", []):
        print(f"#{a['priority']} {a['location']}  |  ~{a['time_gain_s']:.3f}s  |  {a['confidence']} confidence")
        print(f"  Problem:  {a['issue']}")
        print(f"  Fix:      {a['instruction']}")
        print(f"  Evidence: {a['evidence']}")
        print()
    print("SECTOR FEEDBACK")
    print("-"*40)
    for sec in coaching.get("sector_feedback", []):
        dt = sec.get("time_delta_s", 0)
        sign = "+" if dt > 0 else ""
        print(f"{sec['sector']} ({sign}{dt:.3f}s):  {sec['headline']}")
        if sec.get("details"):
            print(f"  {sec['details']}")
        print()
    print("CORNER COACHING")
    print("-"*40)
    for c in coaching.get("corner_coaching", []):
        print(f"{c['corner']} @ {c['dist_m']:.0f}m  |  ~{c['time_gain_s']:.3f}s gain")
        print(f"  Issue:    {c['technique_issue']}")
        print(f"  Fix:      {c['fix']}")
        print(f"  Evidence: {c['data_evidence']}")
        print()
    if coaching.get("positive_observations"):
        print("WHAT'S WORKING")
        print("-"*40)
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
    print(f"Full report saved -> {out_path}")
