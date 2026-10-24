def calculate_gap(student_skills, required_skills):
    student = {s.strip().lower() for s in student_skills}
    required_map = {s.strip().lower(): s for s in required_skills}
    matched = [required_map[k] for k in required_map if k in student]
    missing = [required_map[k] for k in required_map if k not in student]
    percent = round((len(matched) / len(required_skills)) * 100, 2) if required_skills else 0
    return {"matched": matched, "missing": missing, "match_percent": percent,
            "gap_percent": round(100-percent,2)}
