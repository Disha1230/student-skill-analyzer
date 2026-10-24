ORDER = {
    "HTML": 1, "CSS": 2, "JavaScript": 3, "React": 4, "Node.js": 4,
    "Python": 1, "OOP": 2, "SQL": 3, "REST API": 4, "Flask": 4,
    "Pandas": 3, "NumPy": 2, "Power BI": 4, "Git": 5, "GitHub": 5,
    "Java": 1, "Data Structures": 2
}

def build_roadmap(missing):
    return sorted(missing, key=lambda x: ORDER.get(x, 10))

def role_recommendations(student, roles):
    s={x.lower().strip() for x in student}
    out=[]
    for r in roles:
        req=[x.strip() for x in (r["reqs"] or "").split(",")]
        matched=sum(1 for x in req if x.lower() in s)
        pct=round(matched/len(req)*100,2) if req else 0
        item={"role_name":r["role_name"],"description":r["description"],
                    "match_percent":pct,"matched":matched,"total":len(req)}
        if "id" in r.keys():
            item["role_id"]=r["id"]
        out.append(item)
    return out
