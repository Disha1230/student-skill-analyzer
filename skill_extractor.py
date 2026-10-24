import re

# Dictionary-based NLP skill extraction. The dictionary covers multiple
# career domains, not only computer science.
SKILLS = [
    # Technology
    "HTML","CSS","JavaScript","React","Node.js","Python","Java","C","C++","Flask","Django",
    "REST API","SQL","MySQL","MongoDB","Git","GitHub","Pandas","NumPy","Excel","Power BI",
    "Machine Learning","Data Structures","OOP","Cybersecurity","Cloud Computing","Linux","Networking","UI/UX",
    # Commerce
    "Accounting","Bookkeeping","Tally","GST","Taxation","Financial Analysis","Budgeting","Auditing",
    "MS Excel","Financial Reporting","Banking","Investment Analysis","Communication","Business Mathematics",
    # Arts
    "Creative Writing","Content Writing","Research","Critical Thinking","History","Political Science","Sociology",
    "Psychology","Public Speaking","Journalism","Editing","Translation","Social Media","Copywriting",
    "English Language","Writing",
    # Medical / Healthcare
    "Patient Care","First Aid","Anatomy","Physiology","Pharmacology","Medical Terminology","Clinical Skills",
    "Nursing Care","Healthcare Management","Medical Coding","Pathology","Microbiology","Public Health","Nutrition",
    "Empathy","Attention to Detail",
    # Science
    "Biology","Chemistry","Physics","Mathematics","Statistics","Laboratory Skills","Scientific Writing",
    "Data Analysis","Research Methodology","Experiment Design","Environmental Science","Biotechnology","Quality Control","Observation",
    # Education
    "Teaching","Lesson Planning","Classroom Management","Curriculum Design","Assessment","Child Development",
    "Educational Psychology","Presentation Skills","Subject Knowledge","Online Teaching","Communication Skills","Leadership",
    # Design / Media
    "Graphic Design","Adobe Photoshop","Adobe Illustrator","Figma","Canva","Video Editing","Photography","Animation",
    "Typography","Branding","Digital Marketing","SEO","Content Creation","Storytelling","Analytics",
    # Hospitality / Travel
    "Customer Service","Hotel Operations","Food & Beverage","Front Office","Housekeeping","Event Planning","Travel Planning",
    "Tourism","Teamwork","Problem Solving","Time Management"
]

def extract_text_from_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)

def extract_skills_from_text(text):
    text_lower = text.lower()
    found=[]
    for skill in SKILLS:
        pattern = r'(?<![a-z0-9+#])' + re.escape(skill.lower()) + r'(?![a-z0-9+#])'
        if re.search(pattern, text_lower):
            found.append((skill, 1.0))
    return found
