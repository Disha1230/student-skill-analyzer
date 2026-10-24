from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from skill_extractor import extract_skills_from_text, extract_text_from_pdf
from skill_matcher import calculate_gap
from recommender import build_roadmap, role_recommendations
import sqlite3, os, json
from datetime import datetime
import re
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "student_skill_analyzer.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads", "resumes")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "change-this-secret-key-in-production"
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {"pdf"}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'student',
        education TEXT DEFAULT '',
        college TEXT DEFAULT '',
        education_stream TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_name TEXT UNIQUE NOT NULL,
        category TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS student_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        skill_id INTEGER NOT NULL,
        source TEXT DEFAULT 'Manual',
        confidence REAL DEFAULT 1.0,
        UNIQUE(user_id, skill_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS job_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role_name TEXT UNIQUE NOT NULL,
        description TEXT NOT NULL,
        domain TEXT DEFAULT 'Technology'
    );
    CREATE TABLE IF NOT EXISTS job_role_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_role_id INTEGER NOT NULL,
        skill_id INTEGER NOT NULL,
        importance INTEGER DEFAULT 3,
        UNIQUE(job_role_id, skill_id),
        FOREIGN KEY(job_role_id) REFERENCES job_roles(id) ON DELETE CASCADE,
        FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        job_role_id INTEGER NOT NULL,
        matched_count INTEGER NOT NULL,
        missing_count INTEGER NOT NULL,
        match_percent REAL NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(job_role_id) REFERENCES job_roles(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS analysis_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id INTEGER NOT NULL,
        skill_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY(analysis_id) REFERENCES analyses(id) ON DELETE CASCADE,
        FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS learning_resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        url TEXT DEFAULT '',
        difficulty TEXT DEFAULT 'Beginner',
        FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        skill_id INTEGER NOT NULL,
        percentage INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, skill_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
    );
    """)
    # Upgrade older databases without destroying existing student data.
    user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "role" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'student'")
    if "education_stream" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN education_stream TEXT DEFAULT ''")
    # Add domain column when upgrading an older project database.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(job_roles)").fetchall()]
    if "domain" not in cols:
        conn.execute("ALTER TABLE job_roles ADD COLUMN domain TEXT DEFAULT 'Technology'")
    seed_data(conn)
    conn.commit()
    conn.close()

STREAM_DOMAINS = {
    "Commerce": ["Commerce"],
    "Computer Science / IT": ["Technology"],
    "Arts / Humanities": ["Arts"],
    "Science": ["Science"],
    "Medical / Healthcare": ["Medical"],
    "Education": ["Education"],
    "Design / Media": ["Design & Media"],
    "Hospitality / Travel": ["Hospitality & Travel"],
}

def infer_stream_from_skills(skill_names):
    counts = {}
    for name in skill_names:
        row = next((d for d, skills in STREAM_SKILL_HINTS.items() if name in skills), None)
        if row:
            counts[row] = counts.get(row, 0) + 1
    return max(counts, key=counts.get) if counts else ""

STREAM_SKILL_HINTS = {
    "Commerce": {"Accounting","Bookkeeping","Tally","GST","Taxation","Financial Analysis","Budgeting","Auditing","MS Excel","Financial Reporting","Banking","Investment Analysis","Business Mathematics"},
    "Computer Science / IT": {"HTML","CSS","JavaScript","React","Node.js","Python","Java","C","C++","Flask","Django","REST API","SQL","MySQL","MongoDB","Git","GitHub","Pandas","NumPy","Machine Learning","Data Structures","OOP","Cybersecurity","Cloud Computing","Linux","Networking","UI/UX"},
    "Arts / Humanities": {"Creative Writing","Content Writing","Research","Critical Thinking","History","Political Science","Sociology","Psychology","Public Speaking","Journalism","Editing","Translation","Social Media","Copywriting","English Language"},
    "Science": {"Biology","Chemistry","Physics","Mathematics","Statistics","Laboratory Skills","Scientific Writing","Data Analysis","Research Methodology","Experiment Design","Environmental Science","Biotechnology","Quality Control","Observation"},
    "Medical / Healthcare": {"Patient Care","First Aid","Anatomy","Physiology","Pharmacology","Medical Terminology","Clinical Skills","Nursing Care","Healthcare Management","Medical Coding","Pathology","Microbiology","Public Health","Nutrition","Empathy"},
    "Education": {"Teaching","Lesson Planning","Classroom Management","Curriculum Design","Assessment","Child Development","Educational Psychology","Presentation Skills","Subject Knowledge","Online Teaching","Communication Skills","Leadership"},
    "Design / Media": {"Graphic Design","Adobe Photoshop","Adobe Illustrator","Figma","Canva","Video Editing","Photography","Animation","Typography","Branding","Storytelling","Digital Marketing","SEO","Content Creation"},
    "Hospitality / Travel": {"Customer Service","Hotel Operations","Food & Beverage","Front Office","Housekeeping","Event Planning","Travel Planning","Tourism","Teamwork","Problem Solving","Time Management"},
}

def get_student_stream(conn, user_id):
    user = conn.execute("SELECT education_stream FROM users WHERE id=?", (user_id,)).fetchone()
    selected = (user["education_stream"] if user else "") or ""
    if selected in STREAM_DOMAINS:
        return selected
    rows = conn.execute("""SELECT s.skill_name FROM student_skills ss JOIN skills s ON s.id=ss.skill_id WHERE ss.user_id=?""", (user_id,)).fetchall()
    inferred = infer_stream_from_skills([r["skill_name"] for r in rows])
    return inferred or ""

def seed_data(conn):
    # The analyzer is intentionally interdisciplinary: students can select
    # career roles from Technology, Commerce, Arts, Medical/Healthcare,
    # Science, Education, Design/Media, and Hospitality/Travel.
    skills = {
        # Technology
        "HTML":"Technology","CSS":"Technology","JavaScript":"Technology","React":"Technology",
        "Node.js":"Technology","Python":"Technology","Java":"Technology","C":"Technology","C++":"Technology",
        "Flask":"Technology","Django":"Technology","REST API":"Technology","SQL":"Technology",
        "MySQL":"Technology","MongoDB":"Technology","Git":"Technology","GitHub":"Technology",
        "Pandas":"Technology","NumPy":"Technology","Excel":"Technology","Power BI":"Technology",
        "Machine Learning":"Technology","Data Structures":"Technology","OOP":"Technology","Cybersecurity":"Technology",
        "Cloud Computing":"Technology","Linux":"Technology","Networking":"Technology","UI/UX":"Technology",
        # Commerce / Finance
        "Accounting":"Commerce","Bookkeeping":"Commerce","Tally":"Commerce","GST":"Commerce",
        "Taxation":"Commerce","Financial Analysis":"Commerce","Budgeting":"Commerce","Auditing":"Commerce",
        "MS Excel":"Commerce","Financial Reporting":"Commerce","Banking":"Commerce","Investment Analysis":"Commerce",
        "Communication":"Commerce","Business Mathematics":"Commerce",
        # Arts / Humanities
        "Creative Writing":"Arts","Content Writing":"Arts","Research":"Arts","Critical Thinking":"Arts",
        "History":"Arts","Political Science":"Arts","Sociology":"Arts","Psychology":"Arts",
        "Public Speaking":"Arts","Journalism":"Arts","Editing":"Arts","Translation":"Arts",
        "Social Media":"Arts","Copywriting":"Arts","English Language":"Arts",
        # Medical / Healthcare
        "Patient Care":"Medical","First Aid":"Medical","Anatomy":"Medical","Physiology":"Medical",
        "Pharmacology":"Medical","Medical Terminology":"Medical","Clinical Skills":"Medical","Nursing Care":"Medical",
        "Healthcare Management":"Medical","Medical Coding":"Medical","Pathology":"Medical","Microbiology":"Medical",
        "Public Health":"Medical","Nutrition":"Medical","Communication":"Medical","Empathy":"Medical",
        # Science
        "Biology":"Science","Chemistry":"Science","Physics":"Science","Mathematics":"Science",
        "Statistics":"Science","Laboratory Skills":"Science","Scientific Writing":"Science","Data Analysis":"Science",
        "Research Methodology":"Science","Experiment Design":"Science","Environmental Science":"Science",
        "Biotechnology":"Science","Quality Control":"Science","Observation":"Science",
        # Education
        "Teaching":"Education","Lesson Planning":"Education","Classroom Management":"Education",
        "Curriculum Design":"Education","Assessment":"Education","Child Development":"Education",
        "Educational Psychology":"Education","Presentation Skills":"Education","Subject Knowledge":"Education",
        "Online Teaching":"Education","Communication Skills":"Education","Leadership":"Education",
        # Design / Media
        "Graphic Design":"Design & Media","Adobe Photoshop":"Design & Media","Adobe Illustrator":"Design & Media",
        "Figma":"Design & Media","Canva":"Design & Media","Video Editing":"Design & Media","Photography":"Design & Media",
        "Animation":"Design & Media","Typography":"Design & Media","Branding":"Design & Media","Storytelling":"Design & Media",
        "Digital Marketing":"Design & Media","SEO":"Design & Media","Content Creation":"Design & Media",
        # Hospitality / Travel
        "Customer Service":"Hospitality & Travel","Hotel Operations":"Hospitality & Travel","Food & Beverage":"Hospitality & Travel",
        "Front Office":"Hospitality & Travel","Housekeeping":"Hospitality & Travel","Event Planning":"Hospitality & Travel",
        "Travel Planning":"Hospitality & Travel","Tourism":"Hospitality & Travel","Communication Skills":"Hospitality & Travel",
        "Teamwork":"Hospitality & Travel","Problem Solving":"Hospitality & Travel","Time Management":"Hospitality & Travel"
    }
    for name, cat in skills.items():
        conn.execute("INSERT OR IGNORE INTO skills(skill_name, category) VALUES (?,?)", (name,cat))

    roles = {
        'Frontend Developer': ('Technology', 'Supports professional work as a frontend developer through practical skills, analysis and domain knowledge.', ['HTML', 'Python', 'Java', 'C++', 'JavaScript', 'CSS']),
        'Backend Developer': ('Technology', 'Supports professional work as a backend developer through practical skills, analysis and domain knowledge.', ['Java', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'Full Stack Developer': ('Technology', 'Supports professional work as a full stack developer through practical skills, analysis and domain knowledge.', ['C++', 'JavaScript', 'HTML', 'CSS', 'React', 'Node.js']),
        'Data Analyst': ('Technology', 'Supports professional work as a data analyst through practical skills, analysis and domain knowledge.', ['JavaScript', 'HTML', 'CSS', 'React', 'Node.js', 'SQL']),
        'Cybersecurity Analyst': ('Technology', 'Supports professional work as a cybersecurity analyst through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'HTML', 'CSS', 'React', 'Node.js', 'SQL']),
        'UI/UX Designer': ('Technology', 'Supports professional work as a ui/ux designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Software Developer': ('Technology', 'Supports professional work as a software developer through practical skills, analysis and domain knowledge.', ['React', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'Software Engineer': ('Technology', 'Supports professional work as a software engineer through practical skills, analysis and domain knowledge.', ['Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub']),
        'Web Developer': ('Technology', 'Supports professional work as a web developer through practical skills, analysis and domain knowledge.', ['HTML', 'SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub']),
        'Mobile App Developer': ('Technology', 'Supports professional work as a mobile app developer through practical skills, analysis and domain knowledge.', ['MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'Android Developer': ('Technology', 'Supports professional work as a android developer through practical skills, analysis and domain knowledge.', ['MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures', 'OOP']),
        'iOS Developer': ('Technology', 'Supports professional work as a ios developer through practical skills, analysis and domain knowledge.', ['Git', 'GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux']),
        'Java Developer': ('Technology', 'Supports professional work as a java developer through practical skills, analysis and domain knowledge.', ['Java', 'GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux']),
        'Python Developer': ('Technology', 'Supports professional work as a python developer through practical skills, analysis and domain knowledge.', ['Python', 'REST API', 'Data Structures', 'OOP', 'Linux', 'Networking']),
        'C++ Developer': ('Technology', 'Supports professional work as a c++ developer through practical skills, analysis and domain knowledge.', ['C++', 'Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing']),
        'C Developer': ('Technology', 'Supports professional work as a c developer through practical skills, analysis and domain knowledge.', ['OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning']),
        'JavaScript Developer': ('Technology', 'Supports professional work as a javascript developer through practical skills, analysis and domain knowledge.', ['Java', 'JavaScript', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity']),
        'React Developer': ('Technology', 'Supports professional work as a react developer through practical skills, analysis and domain knowledge.', ['React', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas']),
        'Node.js Developer': ('Technology', 'Supports professional work as a node.js developer through practical skills, analysis and domain knowledge.', ['Node.js', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy']),
        'Django Developer': ('Technology', 'Supports professional work as a django developer through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX']),
        'Flask Developer': ('Technology', 'Supports professional work as a flask developer through practical skills, analysis and domain knowledge.', ['Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python']),
        'API Developer': ('Technology', 'Supports professional work as a api developer through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'Database Developer': ('Technology', 'Supports professional work as a database developer through practical skills, analysis and domain knowledge.', ['SQL', 'NumPy', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'SQL Developer': ('Technology', 'Supports professional work as a sql developer through practical skills, analysis and domain knowledge.', ['SQL', 'Power BI', 'UI/UX', 'Python', 'Java', 'C++']),
        'Database Administrator': ('Technology', 'Supports professional work as a database administrator through practical skills, analysis and domain knowledge.', ['SQL', 'UI/UX', 'Python', 'Java', 'C++', 'JavaScript']),
        'Data Engineer': ('Technology', 'Supports professional work as a data engineer through practical skills, analysis and domain knowledge.', ['Python', 'Java', 'C++', 'JavaScript', 'HTML', 'CSS']),
        'Machine Learning Engineer': ('Technology', 'Supports professional work as a machine learning engineer through practical skills, analysis and domain knowledge.', ['Machine Learning', 'Java', 'C++', 'JavaScript', 'HTML', 'CSS']),
        'AI Engineer': ('Technology', 'Supports professional work as a ai engineer through practical skills, analysis and domain knowledge.', ['Machine Learning', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'Data Scientist': ('Technology', 'Supports professional work as a data scientist through practical skills, analysis and domain knowledge.', ['JavaScript', 'HTML', 'CSS', 'React', 'Node.js', 'SQL']),
        'Business Intelligence Analyst': ('Technology', 'Supports professional work as a business intelligence analyst through practical skills, analysis and domain knowledge.', ['HTML', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Power BI Developer': ('Technology', 'Supports professional work as a power bi developer through practical skills, analysis and domain knowledge.', ['Power BI', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Cloud Engineer': ('Technology', 'Supports professional work as a cloud engineer through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'React', 'Node.js', 'SQL', 'MySQL', 'MongoDB']),
        'Cloud Architect': ('Technology', 'Supports professional work as a cloud architect through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'DevOps Engineer': ('Technology', 'Supports professional work as a devops engineer through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub']),
        'Site Reliability Engineer': ('Technology', 'Supports professional work as a site reliability engineer through practical skills, analysis and domain knowledge.', ['MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'System Administrator': ('Technology', 'Supports professional work as a system administrator through practical skills, analysis and domain knowledge.', ['MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures', 'OOP']),
        'Linux Administrator': ('Technology', 'Supports professional work as a linux administrator through practical skills, analysis and domain knowledge.', ['Linux', 'UI/UX', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'Network Administrator': ('Technology', 'Supports professional work as a network administrator through practical skills, analysis and domain knowledge.', ['Networking', 'GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux']),
        'Network Engineer': ('Technology', 'Supports professional work as a network engineer through practical skills, analysis and domain knowledge.', ['Networking', 'REST API', 'Data Structures', 'OOP', 'Linux', 'Cloud Computing']),
        'Cybersecurity Engineer': ('Technology', 'Supports professional work as a cybersecurity engineer through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing']),
        'Security Engineer': ('Technology', 'Supports professional work as a security engineer through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'OOP', 'Linux', 'Networking', 'Cloud Computing', 'Machine Learning']),
        'SOC Analyst': ('Technology', 'Supports professional work as a soc analyst through practical skills, analysis and domain knowledge.', ['Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas']),
        'Penetration Testing Analyst': ('Technology', 'Supports professional work as a penetration testing analyst through practical skills, analysis and domain knowledge.', ['Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy']),
        'Information Security Analyst': ('Technology', 'Supports professional work as a information security analyst through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Cloud Computing', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI']),
        'Ethical Hacker': ('Technology', 'Supports professional work as a ethical hacker through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX']),
        'IT Support Specialist': ('Technology', 'Supports professional work as a it support specialist through practical skills, analysis and domain knowledge.', ['Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python']),
        'Technical Support Engineer': ('Technology', 'Supports professional work as a technical support engineer through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'QA Engineer': ('Technology', 'Supports professional work as a qa engineer through practical skills, analysis and domain knowledge.', ['NumPy', 'Power BI', 'UI/UX', 'Python', 'Java', 'C++']),
        'Software Tester': ('Technology', 'Supports professional work as a software tester through practical skills, analysis and domain knowledge.', ['Power BI', 'UI/UX', 'Python', 'Java', 'C++', 'JavaScript']),
        'Automation Tester': ('Technology', 'Supports professional work as a automation tester through practical skills, analysis and domain knowledge.', ['UI/UX', 'Python', 'Java', 'C++', 'JavaScript', 'HTML']),
        'Selenium Tester': ('Technology', 'Supports professional work as a selenium tester through practical skills, analysis and domain knowledge.', ['Python', 'Java', 'C++', 'JavaScript', 'HTML', 'CSS']),
        'Test Analyst': ('Technology', 'Supports professional work as a test analyst through practical skills, analysis and domain knowledge.', ['Java', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'Performance Tester': ('Technology', 'Supports professional work as a performance tester through practical skills, analysis and domain knowledge.', ['C++', 'JavaScript', 'HTML', 'CSS', 'React', 'Node.js']),
        'Mobile Tester': ('Technology', 'Supports professional work as a mobile tester through practical skills, analysis and domain knowledge.', ['JavaScript', 'HTML', 'CSS', 'React', 'Node.js', 'SQL']),
        'Release Engineer': ('Technology', 'Supports professional work as a release engineer through practical skills, analysis and domain knowledge.', ['HTML', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Build Engineer': ('Technology', 'Supports professional work as a build engineer through practical skills, analysis and domain knowledge.', ['UI/UX', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Configuration Manager': ('Technology', 'Supports professional work as a configuration manager through practical skills, analysis and domain knowledge.', ['React', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'IT Project Coordinator': ('Technology', 'Supports professional work as a it project coordinator through practical skills, analysis and domain knowledge.', ['Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub']),
        'Technical Project Manager': ('Technology', 'Supports professional work as a technical project manager through practical skills, analysis and domain knowledge.', ['SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API']),
        'Product Analyst': ('Technology', 'Supports professional work as a product analyst through practical skills, analysis and domain knowledge.', ['MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'Product Manager': ('Technology', 'Supports professional work as a product manager through practical skills, analysis and domain knowledge.', ['MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures', 'OOP']),
        'Scrum Master': ('Technology', 'Supports professional work as a scrum master through practical skills, analysis and domain knowledge.', ['Git', 'GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux']),
        'Agile Coach': ('Technology', 'Supports professional work as a agile coach through practical skills, analysis and domain knowledge.', ['GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux', 'Networking']),
        'Solutions Architect': ('Technology', 'Supports professional work as a solutions architect through practical skills, analysis and domain knowledge.', ['REST API', 'Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing']),
        'Software Architect': ('Technology', 'Supports professional work as a software architect through practical skills, analysis and domain knowledge.', ['Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity']),
        'Enterprise Architect': ('Technology', 'Supports professional work as a enterprise architect through practical skills, analysis and domain knowledge.', ['OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning']),
        'Systems Analyst': ('Technology', 'Supports professional work as a systems analyst through practical skills, analysis and domain knowledge.', ['Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas']),
        'Business Systems Analyst': ('Technology', 'Supports professional work as a business systems analyst through practical skills, analysis and domain knowledge.', ['Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy']),
        'Technical Business Analyst': ('Technology', 'Supports professional work as a technical business analyst through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI']),
        'ERP Consultant': ('Technology', 'Supports professional work as a erp consultant through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX']),
        'CRM Developer': ('Technology', 'Supports professional work as a crm developer through practical skills, analysis and domain knowledge.', ['Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python']),
        'Salesforce Administrator': ('Technology', 'Supports professional work as a salesforce administrator through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'Salesforce Developer': ('Technology', 'Supports professional work as a salesforce developer through practical skills, analysis and domain knowledge.', ['NumPy', 'Power BI', 'UI/UX', 'Python', 'Java', 'C++']),
        'ServiceNow Developer': ('Technology', 'Supports professional work as a servicenow developer through practical skills, analysis and domain knowledge.', ['Power BI', 'UI/UX', 'Python', 'Java', 'C++', 'JavaScript']),
        'SharePoint Developer': ('Technology', 'Supports professional work as a sharepoint developer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Python', 'Java', 'C++', 'JavaScript', 'HTML']),
        'Blockchain Developer': ('Technology', 'Supports professional work as a blockchain developer through practical skills, analysis and domain knowledge.', ['Python', 'Java', 'C++', 'JavaScript', 'HTML', 'CSS']),
        'Smart Contract Developer': ('Technology', 'Supports professional work as a smart contract developer through practical skills, analysis and domain knowledge.', ['Java', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'Game Developer': ('Technology', 'Supports professional work as a game developer through practical skills, analysis and domain knowledge.', ['C++', 'JavaScript', 'HTML', 'CSS', 'React', 'Node.js']),
        'Game Designer': ('Technology', 'Supports professional work as a game designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'JavaScript', 'HTML', 'CSS', 'React', 'Node.js']),
        'AR/VR Developer': ('Technology', 'Supports professional work as a ar/vr developer through practical skills, analysis and domain knowledge.', ['HTML', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Embedded Systems Engineer': ('Technology', 'Supports professional work as a embedded systems engineer through practical skills, analysis and domain knowledge.', ['CSS', 'React', 'Node.js', 'SQL', 'MySQL', 'MongoDB']),
        'IoT Engineer': ('Technology', 'Supports professional work as a iot engineer through practical skills, analysis and domain knowledge.', ['React', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'Firmware Engineer': ('Technology', 'Supports professional work as a firmware engineer through practical skills, analysis and domain knowledge.', ['Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub']),
        'Robotics Engineer': ('Technology', 'Supports professional work as a robotics engineer through practical skills, analysis and domain knowledge.', ['SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API']),
        'Computer Vision Engineer': ('Technology', 'Supports professional work as a computer vision engineer through practical skills, analysis and domain knowledge.', ['MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'NLP Engineer': ('Technology', 'Supports professional work as a nlp engineer through practical skills, analysis and domain knowledge.', ['MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures', 'OOP']),
        'MLOps Engineer': ('Technology', 'Supports professional work as a mlops engineer through practical skills, analysis and domain knowledge.', ['Git', 'GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux']),
        'Data Visualization Developer': ('Technology', 'Supports professional work as a data visualization developer through practical skills, analysis and domain knowledge.', ['GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux', 'Networking']),
        'ETL Developer': ('Technology', 'Supports professional work as a etl developer through practical skills, analysis and domain knowledge.', ['REST API', 'Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing']),
        'Data Quality Analyst': ('Technology', 'Supports professional work as a data quality analyst through practical skills, analysis and domain knowledge.', ['Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity']),
        'Data Governance Analyst': ('Technology', 'Supports professional work as a data governance analyst through practical skills, analysis and domain knowledge.', ['OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning']),
        'Database Analyst': ('Technology', 'Supports professional work as a database analyst through practical skills, analysis and domain knowledge.', ['SQL', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning']),
        'BI Consultant': ('Technology', 'Supports professional work as a bi consultant through practical skills, analysis and domain knowledge.', ['Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy']),
        'Cloud Support Engineer': ('Technology', 'Supports professional work as a cloud support engineer through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI']),
        'Cloud Security Engineer': ('Technology', 'Supports professional work as a cloud security engineer through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Cloud Computing', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI']),
        'DevSecOps Engineer': ('Technology', 'Supports professional work as a devsecops engineer through practical skills, analysis and domain knowledge.', ['Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python']),
        'Platform Engineer': ('Technology', 'Supports professional work as a platform engineer through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'Infrastructure Engineer': ('Technology', 'Supports professional work as a infrastructure engineer through practical skills, analysis and domain knowledge.', ['NumPy', 'Power BI', 'UI/UX', 'Python', 'Java', 'C++']),
        'Network Security Analyst': ('Technology', 'Supports professional work as a network security analyst through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Networking', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'Digital Forensics Analyst': ('Technology', 'Supports professional work as a digital forensics analyst through practical skills, analysis and domain knowledge.', ['UI/UX', 'Python', 'Java', 'C++', 'JavaScript', 'HTML']),
        'Risk Analyst - Cybersecurity': ('Technology', 'Supports professional work as a risk analyst - cybersecurity through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Python', 'Java', 'C++', 'JavaScript', 'HTML']),
        'Identity and Access Management Analyst': ('Technology', 'Supports professional work as a identity and access management analyst through practical skills, analysis and domain knowledge.', ['Java', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'IT Auditor': ('Technology', 'Supports professional work as a it auditor through practical skills, analysis and domain knowledge.', ['C++', 'JavaScript', 'HTML', 'CSS', 'React', 'Node.js']),
        'Technology Consultant': ('Technology', 'Supports professional work as a technology consultant through practical skills, analysis and domain knowledge.', ['JavaScript', 'HTML', 'CSS', 'React', 'Node.js', 'SQL']),
        'Technical Writer': ('Technology', 'Supports professional work as a technical writer through practical skills, analysis and domain knowledge.', ['HTML', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Web Accessibility Specialist': ('Technology', 'Supports professional work as a web accessibility specialist through practical skills, analysis and domain knowledge.', ['HTML', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'SEO Technical Analyst': ('Technology', 'Supports professional work as a seo technical analyst through practical skills, analysis and domain knowledge.', ['React', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'Webmaster': ('Technology', 'Supports professional work as a webmaster through practical skills, analysis and domain knowledge.', ['HTML', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'IT Operations Analyst': ('Technology', 'Supports professional work as a it operations analyst through practical skills, analysis and domain knowledge.', ['SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API']),
        'Application Support Analyst': ('Technology', 'Supports professional work as a application support analyst through practical skills, analysis and domain knowledge.', ['MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'Incident Manager': ('Technology', 'Supports professional work as a incident manager through practical skills, analysis and domain knowledge.', ['MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures', 'OOP']),
        'Change Manager': ('Technology', 'Supports professional work as a change manager through practical skills, analysis and domain knowledge.', ['Git', 'GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux']),
        'Service Delivery Analyst': ('Technology', 'Supports professional work as a service delivery analyst through practical skills, analysis and domain knowledge.', ['GitHub', 'REST API', 'Data Structures', 'OOP', 'Linux', 'Networking']),
        'Help Desk Technician': ('Technology', 'Supports professional work as a help desk technician through practical skills, analysis and domain knowledge.', ['REST API', 'Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing']),
        'Computer Technician': ('Technology', 'Supports professional work as a computer technician through practical skills, analysis and domain knowledge.', ['Data Structures', 'OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity']),
        'System Integration Engineer': ('Technology', 'Supports professional work as a system integration engineer through practical skills, analysis and domain knowledge.', ['OOP', 'Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning']),
        'Integration Developer': ('Technology', 'Supports professional work as a integration developer through practical skills, analysis and domain knowledge.', ['Linux', 'Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas']),
        'Middleware Developer': ('Technology', 'Supports professional work as a middleware developer through practical skills, analysis and domain knowledge.', ['Networking', 'Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy']),
        'API Integration Specialist': ('Technology', 'Supports professional work as a api integration specialist through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI']),
        'RPA Developer': ('Technology', 'Supports professional work as a rpa developer through practical skills, analysis and domain knowledge.', ['Cybersecurity', 'Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX']),
        'Automation Engineer': ('Technology', 'Supports professional work as a automation engineer through practical skills, analysis and domain knowledge.', ['Machine Learning', 'Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python']),
        'Low-Code Developer': ('Technology', 'Supports professional work as a low-code developer through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Power BI', 'UI/UX', 'Python', 'Java']),
        'No-Code Solutions Specialist': ('Technology', 'Supports professional work as a no-code solutions specialist through practical skills, analysis and domain knowledge.', ['NumPy', 'Power BI', 'UI/UX', 'Python', 'Java', 'C++']),
        'Data Migration Specialist': ('Technology', 'Supports professional work as a data migration specialist through practical skills, analysis and domain knowledge.', ['Power BI', 'UI/UX', 'Python', 'Java', 'C++', 'JavaScript']),
        'Database Migration Engineer': ('Technology', 'Supports professional work as a database migration engineer through practical skills, analysis and domain knowledge.', ['SQL', 'UI/UX', 'Python', 'Java', 'C++', 'JavaScript']),
        'Research Software Engineer': ('Technology', 'Supports professional work as a research software engineer through practical skills, analysis and domain knowledge.', ['Python', 'Java', 'C++', 'JavaScript', 'HTML', 'CSS']),
        'Computer Science Research Assistant': ('Technology', 'Supports professional work as a computer science research assistant through practical skills, analysis and domain knowledge.', ['Java', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'AI Research Assistant': ('Technology', 'Supports professional work as a ai research assistant through practical skills, analysis and domain knowledge.', ['Machine Learning', 'C++', 'JavaScript', 'HTML', 'CSS', 'React']),
        'Cloud Solutions Consultant': ('Technology', 'Supports professional work as a cloud solutions consultant through practical skills, analysis and domain knowledge.', ['Cloud Computing', 'JavaScript', 'HTML', 'CSS', 'React', 'Node.js']),
        'Pre-Sales Engineer': ('Technology', 'Supports professional work as a pre-sales engineer through practical skills, analysis and domain knowledge.', ['HTML', 'CSS', 'React', 'Node.js', 'SQL', 'MySQL']),
        'Technical Account Manager': ('Technology', 'Supports professional work as a technical account manager through practical skills, analysis and domain knowledge.', ['CSS', 'React', 'Node.js', 'SQL', 'MySQL', 'MongoDB']),
        'Developer Advocate': ('Technology', 'Supports professional work as a developer advocate through practical skills, analysis and domain knowledge.', ['React', 'Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git']),
        'Open Source Program Coordinator': ('Technology', 'Supports professional work as a open source program coordinator through practical skills, analysis and domain knowledge.', ['Node.js', 'SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub']),
        'IT Trainer': ('Technology', 'Supports professional work as a it trainer through practical skills, analysis and domain knowledge.', ['SQL', 'MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API']),
        'Coding Instructor': ('Technology', 'Supports professional work as a coding instructor through practical skills, analysis and domain knowledge.', ['MySQL', 'MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures']),
        'Technology Program Manager': ('Technology', 'Supports professional work as a technology program manager through practical skills, analysis and domain knowledge.', ['MongoDB', 'Git', 'GitHub', 'REST API', 'Data Structures', 'OOP']),
        'Accountant': ('Commerce', 'Supports professional work as a accountant through practical skills, analysis and domain knowledge.', ['Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Financial Analyst': ('Commerce', 'Supports professional work as a financial analyst through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Tally', 'GST', 'Taxation', 'Budgeting', 'Auditing']),
        'Banking Associate': ('Commerce', 'Supports professional work as a banking associate through practical skills, analysis and domain knowledge.', ['Banking', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing']),
        'Tax Assistant': ('Commerce', 'Supports professional work as a tax assistant through practical skills, analysis and domain knowledge.', ['Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Accounts Payable Specialist': ('Commerce', 'Supports professional work as a accounts payable specialist through practical skills, analysis and domain knowledge.', ['Accounting', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Accounts Receivable Specialist': ('Commerce', 'Supports professional work as a accounts receivable specialist through practical skills, analysis and domain knowledge.', ['Accounting', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking']),
        'Payroll Specialist': ('Commerce', 'Supports professional work as a payroll specialist through practical skills, analysis and domain knowledge.', ['Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication']),
        'Audit Assistant': ('Commerce', 'Supports professional work as a audit assistant through practical skills, analysis and domain knowledge.', ['Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication']),
        'Internal Auditor': ('Commerce', 'Supports professional work as a internal auditor through practical skills, analysis and domain knowledge.', ['Auditing', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics']),
        'External Audit Associate': ('Commerce', 'Supports professional work as a external audit associate through practical skills, analysis and domain knowledge.', ['Auditing', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel']),
        'Tax Consultant': ('Commerce', 'Supports professional work as a tax consultant through practical skills, analysis and domain knowledge.', ['Taxation', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'GST Consultant': ('Commerce', 'Supports professional work as a gst consultant through practical skills, analysis and domain knowledge.', ['GST', 'Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping']),
        'Financial Accountant': ('Commerce', 'Supports professional work as a financial accountant through practical skills, analysis and domain knowledge.', ['Accounting', 'Financial Analysis', 'Business Mathematics', 'Excel', 'Bookkeeping', 'Tally']),
        'Management Accountant': ('Commerce', 'Supports professional work as a management accountant through practical skills, analysis and domain knowledge.', ['Accounting', 'Excel', 'Bookkeeping', 'Tally', 'GST', 'Taxation']),
        'Cost Accountant': ('Commerce', 'Supports professional work as a cost accountant through practical skills, analysis and domain knowledge.', ['Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Junior Accountant': ('Commerce', 'Supports professional work as a junior accountant through practical skills, analysis and domain knowledge.', ['Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Senior Accountant': ('Commerce', 'Supports professional work as a senior accountant through practical skills, analysis and domain knowledge.', ['Accounting', 'Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting']),
        'Bookkeeper': ('Commerce', 'Supports professional work as a bookkeeper through practical skills, analysis and domain knowledge.', ['GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel']),
        'Billing Executive': ('Commerce', 'Supports professional work as a billing executive through practical skills, analysis and domain knowledge.', ['Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Accounts Executive': ('Commerce', 'Supports professional work as a accounts executive through practical skills, analysis and domain knowledge.', ['Accounting', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Finance Executive': ('Commerce', 'Supports professional work as a finance executive through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking']),
        'Finance Manager': ('Commerce', 'Supports professional work as a finance manager through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis']),
        'Treasury Analyst': ('Commerce', 'Supports professional work as a treasury analyst through practical skills, analysis and domain knowledge.', ['MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics']),
        'Credit Analyst': ('Commerce', 'Supports professional work as a credit analyst through practical skills, analysis and domain knowledge.', ['Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel']),
        'Loan Officer': ('Commerce', 'Supports professional work as a loan officer through practical skills, analysis and domain knowledge.', ['Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'Bank Officer': ('Commerce', 'Supports professional work as a bank officer through practical skills, analysis and domain knowledge.', ['Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'Banking Operations Executive': ('Commerce', 'Supports professional work as a banking operations executive through practical skills, analysis and domain knowledge.', ['Banking', 'Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping']),
        'Relationship Manager - Banking': ('Commerce', 'Supports professional work as a relationship manager - banking through practical skills, analysis and domain knowledge.', ['Banking', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally']),
        'KYC Analyst': ('Commerce', 'Supports professional work as a kyc analyst through practical skills, analysis and domain knowledge.', ['Excel', 'Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation']),
        'Compliance Analyst': ('Commerce', 'Supports professional work as a compliance analyst through practical skills, analysis and domain knowledge.', ['Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Investment Analyst': ('Commerce', 'Supports professional work as a investment analyst through practical skills, analysis and domain knowledge.', ['Investment Analysis', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Equity Research Analyst': ('Commerce', 'Supports professional work as a equity research analyst through practical skills, analysis and domain knowledge.', ['Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing']),
        'Credit Risk Analyst': ('Commerce', 'Supports professional work as a credit risk analyst through practical skills, analysis and domain knowledge.', ['GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel']),
        'Financial Risk Analyst': ('Commerce', 'Supports professional work as a financial risk analyst through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Taxation', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Portfolio Analyst': ('Commerce', 'Supports professional work as a portfolio analyst through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking']),
        'Wealth Management Associate': ('Commerce', 'Supports professional work as a wealth management associate through practical skills, analysis and domain knowledge.', ['Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis']),
        'Insurance Associate': ('Commerce', 'Supports professional work as a insurance associate through practical skills, analysis and domain knowledge.', ['Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication']),
        'Insurance Underwriter': ('Commerce', 'Supports professional work as a insurance underwriter through practical skills, analysis and domain knowledge.', ['MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics']),
        'Insurance Claims Analyst': ('Commerce', 'Supports professional work as a insurance claims analyst through practical skills, analysis and domain knowledge.', ['Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel']),
        'Actuarial Assistant': ('Commerce', 'Supports professional work as a actuarial assistant through practical skills, analysis and domain knowledge.', ['Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'Investment Operations Associate': ('Commerce', 'Supports professional work as a investment operations associate through practical skills, analysis and domain knowledge.', ['Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping']),
        'Mutual Fund Executive': ('Commerce', 'Supports professional work as a mutual fund executive through practical skills, analysis and domain knowledge.', ['Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally']),
        'Stock Market Analyst': ('Commerce', 'Supports professional work as a stock market analyst through practical skills, analysis and domain knowledge.', ['Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally', 'GST']),
        'Securities Operations Analyst': ('Commerce', 'Supports professional work as a securities operations analyst through practical skills, analysis and domain knowledge.', ['Excel', 'Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation']),
        'Financial Planning Associate': ('Commerce', 'Supports professional work as a financial planning associate through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation']),
        'Business Analyst - Finance': ('Commerce', 'Supports professional work as a business analyst - finance through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Budgeting']),
        'MIS Executive': ('Commerce', 'Supports professional work as a mis executive through practical skills, analysis and domain knowledge.', ['Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing']),
        'MIS Analyst': ('Commerce', 'Supports professional work as a mis analyst through practical skills, analysis and domain knowledge.', ['GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel']),
        'Business Operations Analyst': ('Commerce', 'Supports professional work as a business operations analyst through practical skills, analysis and domain knowledge.', ['Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Procurement Executive': ('Commerce', 'Supports professional work as a procurement executive through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking']),
        'Purchasing Analyst': ('Commerce', 'Supports professional work as a purchasing analyst through practical skills, analysis and domain knowledge.', ['Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis']),
        'Supply Chain Analyst': ('Commerce', 'Supports professional work as a supply chain analyst through practical skills, analysis and domain knowledge.', ['Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication']),
        'Inventory Analyst': ('Commerce', 'Supports professional work as a inventory analyst through practical skills, analysis and domain knowledge.', ['MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics']),
        'Logistics Coordinator': ('Commerce', 'Supports professional work as a logistics coordinator through practical skills, analysis and domain knowledge.', ['Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel']),
        'Operations Executive': ('Commerce', 'Supports professional work as a operations executive through practical skills, analysis and domain knowledge.', ['Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'Business Development Executive': ('Commerce', 'Supports professional work as a business development executive through practical skills, analysis and domain knowledge.', ['Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping']),
        'Sales Executive': ('Commerce', 'Supports professional work as a sales executive through practical skills, analysis and domain knowledge.', ['Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally']),
        'Sales Analyst': ('Commerce', 'Supports professional work as a sales analyst through practical skills, analysis and domain knowledge.', ['Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally']),
        'Marketing Executive': ('Commerce', 'Supports professional work as a marketing executive through practical skills, analysis and domain knowledge.', ['Excel', 'Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation']),
        'Market Research Analyst': ('Commerce', 'Supports professional work as a market research analyst through practical skills, analysis and domain knowledge.', ['Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Customer Relationship Executive': ('Commerce', 'Supports professional work as a customer relationship executive through practical skills, analysis and domain knowledge.', ['Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting']),
        'Client Relationship Manager': ('Commerce', 'Supports professional work as a client relationship manager through practical skills, analysis and domain knowledge.', ['Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing']),
        'Retail Banking Executive': ('Commerce', 'Supports professional work as a retail banking executive through practical skills, analysis and domain knowledge.', ['Banking', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing']),
        'Branch Operations Executive': ('Commerce', 'Supports professional work as a branch operations executive through practical skills, analysis and domain knowledge.', ['Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Trade Finance Executive': ('Commerce', 'Supports professional work as a trade finance executive through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking']),
        'Import Export Executive': ('Commerce', 'Supports professional work as a import export executive through practical skills, analysis and domain knowledge.', ['Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis']),
        'Accounts Receivable Analyst': ('Commerce', 'Supports professional work as a accounts receivable analyst through practical skills, analysis and domain knowledge.', ['Accounting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis']),
        'Accounts Payable Analyst': ('Commerce', 'Supports professional work as a accounts payable analyst through practical skills, analysis and domain knowledge.', ['Accounting', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication']),
        'Financial Reporting Analyst': ('Commerce', 'Supports professional work as a financial reporting analyst through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics']),
        'Budget Analyst': ('Commerce', 'Supports professional work as a budget analyst through practical skills, analysis and domain knowledge.', ['Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'Revenue Analyst': ('Commerce', 'Supports professional work as a revenue analyst through practical skills, analysis and domain knowledge.', ['Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping']),
        'Pricing Analyst': ('Commerce', 'Supports professional work as a pricing analyst through practical skills, analysis and domain knowledge.', ['Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally']),
        'Business Controller': ('Commerce', 'Supports professional work as a business controller through practical skills, analysis and domain knowledge.', ['Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping', 'Tally', 'GST']),
        'Finance Operations Analyst': ('Commerce', 'Supports professional work as a finance operations analyst through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Excel', 'Accounting', 'Bookkeeping', 'Tally', 'GST']),
        'Commercial Analyst': ('Commerce', 'Supports professional work as a commercial analyst through practical skills, analysis and domain knowledge.', ['Accounting', 'Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis']),
        'Cost Control Analyst': ('Commerce', 'Supports professional work as a cost control analyst through practical skills, analysis and domain knowledge.', ['Bookkeeping', 'Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting']),
        'Procurement Analyst': ('Commerce', 'Supports professional work as a procurement analyst through practical skills, analysis and domain knowledge.', ['Tally', 'GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing']),
        'Vendor Management Executive': ('Commerce', 'Supports professional work as a vendor management executive through practical skills, analysis and domain knowledge.', ['GST', 'Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel']),
        'Contract Administrator': ('Commerce', 'Supports professional work as a contract administrator through practical skills, analysis and domain knowledge.', ['Taxation', 'Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting']),
        'Business Administration Executive': ('Commerce', 'Supports professional work as a business administration executive through practical skills, analysis and domain knowledge.', ['Financial Analysis', 'Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking']),
        'Office Administrator': ('Commerce', 'Supports professional work as a office administrator through practical skills, analysis and domain knowledge.', ['Budgeting', 'Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis']),
        'Data Entry Executive': ('Commerce', 'Supports professional work as a data entry executive through practical skills, analysis and domain knowledge.', ['Auditing', 'MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication']),
        'Business Coordinator': ('Commerce', 'Supports professional work as a business coordinator through practical skills, analysis and domain knowledge.', ['MS Excel', 'Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics']),
        'Management Trainee - Commerce': ('Commerce', 'Supports professional work as a management trainee - commerce through practical skills, analysis and domain knowledge.', ['Financial Reporting', 'Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel']),
        'Entrepreneurship Coordinator': ('Commerce', 'Supports professional work as a entrepreneurship coordinator through practical skills, analysis and domain knowledge.', ['Banking', 'Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting']),
        'Small Business Consultant': ('Commerce', 'Supports professional work as a small business consultant through practical skills, analysis and domain knowledge.', ['Investment Analysis', 'Communication', 'Business Mathematics', 'Excel', 'Accounting', 'Bookkeeping']),
        'Content Writer': ('Arts', 'Supports professional work as a content writer through practical skills, analysis and domain knowledge.', ['Content Writing', 'Writing', 'Research', 'Critical Thinking', 'History', 'Political Science']),
        'Journalist': ('Arts', 'Supports professional work as a journalist through practical skills, analysis and domain knowledge.', ['Journalism', 'Research', 'Critical Thinking', 'History', 'Political Science', 'Sociology']),
        'Social Media Executive': ('Arts', 'Supports professional work as a social media executive through practical skills, analysis and domain knowledge.', ['Social Media', 'Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology']),
        'Psychology Assistant': ('Arts', 'Supports professional work as a psychology assistant through practical skills, analysis and domain knowledge.', ['Psychology', 'History', 'Political Science', 'Sociology', 'Public Speaking', 'Journalism']),
        'Copywriter': ('Arts', 'Supports professional work as a copywriter through practical skills, analysis and domain knowledge.', ['Writing', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism']),
        'Editor': ('Arts', 'Supports professional work as a editor through practical skills, analysis and domain knowledge.', ['Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation']),
        'Proofreader': ('Arts', 'Supports professional work as a proofreader through practical skills, analysis and domain knowledge.', ['Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media']),
        'Technical Content Writer': ('Arts', 'Supports professional work as a technical content writer through practical skills, analysis and domain knowledge.', ['Content Writing', 'Writing', 'Public Speaking', 'Journalism', 'Editing', 'Translation']),
        'Creative Writer': ('Arts', 'Supports professional work as a creative writer through practical skills, analysis and domain knowledge.', ['Writing', 'Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting']),
        'Script Writer': ('Arts', 'Supports professional work as a script writer through practical skills, analysis and domain knowledge.', ['Writing', 'Editing', 'Translation', 'Social Media', 'Copywriting', 'English Language']),
        'Screenwriter': ('Arts', 'Supports professional work as a screenwriter through practical skills, analysis and domain knowledge.', ['Writing', 'Translation', 'Social Media', 'Copywriting', 'English Language', 'Communication']),
        'Blogger': ('Arts', 'Supports professional work as a blogger through practical skills, analysis and domain knowledge.', ['Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing']),
        'News Reporter': ('Arts', 'Supports professional work as a news reporter through practical skills, analysis and domain knowledge.', ['Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing', 'Content Writing']),
        'News Editor': ('Arts', 'Supports professional work as a news editor through practical skills, analysis and domain knowledge.', ['English Language', 'Writing', 'Communication', 'Creative Writing', 'Content Writing', 'Research']),
        'Photojournalist': ('Arts', 'Supports professional work as a photojournalist through practical skills, analysis and domain knowledge.', ['Journalism', 'Writing', 'Communication', 'Creative Writing', 'Content Writing', 'Research']),
        'Broadcast Journalist': ('Arts', 'Supports professional work as a broadcast journalist through practical skills, analysis and domain knowledge.', ['Journalism', 'Communication', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking']),
        'Digital Journalist': ('Arts', 'Supports professional work as a digital journalist through practical skills, analysis and domain knowledge.', ['Journalism', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History']),
        'Research Writer': ('Arts', 'Supports professional work as a research writer through practical skills, analysis and domain knowledge.', ['Writing', 'Research', 'Content Writing', 'Critical Thinking', 'History', 'Political Science']),
        'Academic Writer': ('Arts', 'Supports professional work as a academic writer through practical skills, analysis and domain knowledge.', ['Writing', 'Research', 'Critical Thinking', 'History', 'Political Science', 'Sociology']),
        'Grant Writer': ('Arts', 'Supports professional work as a grant writer through practical skills, analysis and domain knowledge.', ['Writing', 'Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology']),
        'Proposal Writer': ('Arts', 'Supports professional work as a proposal writer through practical skills, analysis and domain knowledge.', ['Writing', 'History', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking']),
        'Documentation Specialist': ('Arts', 'Supports professional work as a documentation specialist through practical skills, analysis and domain knowledge.', ['Political Science', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing']),
        'Communications Executive': ('Arts', 'Supports professional work as a communications executive through practical skills, analysis and domain knowledge.', ['Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation']),
        'Public Relations Executive': ('Arts', 'Supports professional work as a public relations executive through practical skills, analysis and domain knowledge.', ['Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media']),
        'PR Assistant': ('Arts', 'Supports professional work as a pr assistant through practical skills, analysis and domain knowledge.', ['Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting']),
        'Corporate Communications Executive': ('Arts', 'Supports professional work as a corporate communications executive through practical skills, analysis and domain knowledge.', ['Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting', 'English Language']),
        'Internal Communications Coordinator': ('Arts', 'Supports professional work as a internal communications coordinator through practical skills, analysis and domain knowledge.', ['Editing', 'Translation', 'Social Media', 'Copywriting', 'English Language', 'Writing']),
        'Community Manager': ('Arts', 'Supports professional work as a community manager through practical skills, analysis and domain knowledge.', ['Translation', 'Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication']),
        'Social Media Manager': ('Arts', 'Supports professional work as a social media manager through practical skills, analysis and domain knowledge.', ['Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing']),
        'Social Media Coordinator': ('Arts', 'Supports professional work as a social media coordinator through practical skills, analysis and domain knowledge.', ['Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing']),
        'Content Strategist': ('Arts', 'Supports professional work as a content strategist through practical skills, analysis and domain knowledge.', ['Content Writing', 'English Language', 'Writing', 'Communication', 'Creative Writing', 'Research']),
        'Content Editor': ('Arts', 'Supports professional work as a content editor through practical skills, analysis and domain knowledge.', ['Content Writing', 'Writing', 'Communication', 'Creative Writing', 'Research', 'Critical Thinking']),
        'Content Marketing Writer': ('Arts', 'Supports professional work as a content marketing writer through practical skills, analysis and domain knowledge.', ['Content Writing', 'Writing', 'Communication', 'Creative Writing', 'Research', 'Critical Thinking']),
        'Brand Copywriter': ('Arts', 'Supports professional work as a brand copywriter through practical skills, analysis and domain knowledge.', ['Writing', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History']),
        'Advertising Copywriter': ('Arts', 'Supports professional work as a advertising copywriter through practical skills, analysis and domain knowledge.', ['Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History', 'Political Science']),
        'SEO Content Writer': ('Arts', 'Supports professional work as a seo content writer through practical skills, analysis and domain knowledge.', ['Content Writing', 'Writing', 'Research', 'Critical Thinking', 'History', 'Political Science']),
        'Email Marketing Writer': ('Arts', 'Supports professional work as a email marketing writer through practical skills, analysis and domain knowledge.', ['Writing', 'Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology']),
        'UX Writer': ('Arts', 'Supports professional work as a ux writer through practical skills, analysis and domain knowledge.', ['Writing', 'History', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking']),
        'Content Researcher': ('Arts', 'Supports professional work as a content researcher through practical skills, analysis and domain knowledge.', ['Content Writing', 'Research', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking']),
        'Market Research Assistant': ('Arts', 'Supports professional work as a market research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing']),
        'Research Assistant - Humanities': ('Arts', 'Supports professional work as a research assistant - humanities through practical skills, analysis and domain knowledge.', ['Research', 'Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation']),
        'Research Analyst - Social Science': ('Arts', 'Supports professional work as a research analyst - social science through practical skills, analysis and domain knowledge.', ['Research', 'Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media']),
        'Sociology Research Assistant': ('Arts', 'Supports professional work as a sociology research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting']),
        'Political Science Research Assistant': ('Arts', 'Supports professional work as a political science research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Editing', 'Translation', 'Social Media', 'Copywriting', 'English Language']),
        'History Research Assistant': ('Arts', 'Supports professional work as a history research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Translation', 'Social Media', 'Copywriting', 'English Language', 'Writing']),
        'Psychology Research Assistant': ('Arts', 'Supports professional work as a psychology research assistant through practical skills, analysis and domain knowledge.', ['Psychology', 'Research', 'Social Media', 'Copywriting', 'English Language', 'Writing']),
        'Public Policy Research Assistant': ('Arts', 'Supports professional work as a public policy research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing']),
        'Library Assistant': ('Arts', 'Supports professional work as a library assistant through practical skills, analysis and domain knowledge.', ['English Language', 'Writing', 'Communication', 'Creative Writing', 'Content Writing', 'Research']),
        'Archivist': ('Arts', 'Supports professional work as a archivist through practical skills, analysis and domain knowledge.', ['Writing', 'Communication', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking']),
        'Museum Assistant': ('Arts', 'Supports professional work as a museum assistant through practical skills, analysis and domain knowledge.', ['Communication', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History']),
        'Curatorial Assistant': ('Arts', 'Supports professional work as a curatorial assistant through practical skills, analysis and domain knowledge.', ['Creative Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History', 'Political Science']),
        'Heritage Coordinator': ('Arts', 'Supports professional work as a heritage coordinator through practical skills, analysis and domain knowledge.', ['Content Writing', 'Research', 'Critical Thinking', 'History', 'Political Science', 'Sociology']),
        'Translator': ('Arts', 'Supports professional work as a translator through practical skills, analysis and domain knowledge.', ['Research', 'Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology']),
        'Interpreter': ('Arts', 'Supports professional work as a interpreter through practical skills, analysis and domain knowledge.', ['Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking']),
        'Language Trainer': ('Arts', 'Supports professional work as a language trainer through practical skills, analysis and domain knowledge.', ['History', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism']),
        'English Trainer': ('Arts', 'Supports professional work as a english trainer through practical skills, analysis and domain knowledge.', ['Political Science', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing']),
        'Proofreading Assistant': ('Arts', 'Supports professional work as a proofreading assistant through practical skills, analysis and domain knowledge.', ['Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation']),
        'Publishing Assistant': ('Arts', 'Supports professional work as a publishing assistant through practical skills, analysis and domain knowledge.', ['Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media']),
        'Editorial Assistant': ('Arts', 'Supports professional work as a editorial assistant through practical skills, analysis and domain knowledge.', ['Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting']),
        'Literary Assistant': ('Arts', 'Supports professional work as a literary assistant through practical skills, analysis and domain knowledge.', ['Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting', 'English Language']),
        'Legal Content Writer': ('Arts', 'Supports professional work as a legal content writer through practical skills, analysis and domain knowledge.', ['Content Writing', 'Writing', 'Editing', 'Translation', 'Social Media', 'Copywriting']),
        'Legal Research Assistant': ('Arts', 'Supports professional work as a legal research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Translation', 'Social Media', 'Copywriting', 'English Language', 'Writing']),
        'Paralegal Assistant': ('Arts', 'Supports professional work as a paralegal assistant through practical skills, analysis and domain knowledge.', ['Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing']),
        'Human Resources Coordinator': ('Arts', 'Supports professional work as a human resources coordinator through practical skills, analysis and domain knowledge.', ['Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing', 'Content Writing']),
        'Recruitment Coordinator': ('Arts', 'Supports professional work as a recruitment coordinator through practical skills, analysis and domain knowledge.', ['English Language', 'Writing', 'Communication', 'Creative Writing', 'Content Writing', 'Research']),
        'Talent Acquisition Associate': ('Arts', 'Supports professional work as a talent acquisition associate through practical skills, analysis and domain knowledge.', ['Writing', 'Communication', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking']),
        'Training Coordinator': ('Arts', 'Supports professional work as a training coordinator through practical skills, analysis and domain knowledge.', ['Communication', 'Creative Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History']),
        'Employee Relations Assistant': ('Arts', 'Supports professional work as a employee relations assistant through practical skills, analysis and domain knowledge.', ['Creative Writing', 'Content Writing', 'Research', 'Critical Thinking', 'History', 'Political Science']),
        'Learning and Development Coordinator': ('Arts', 'Supports professional work as a learning and development coordinator through practical skills, analysis and domain knowledge.', ['Content Writing', 'Research', 'Critical Thinking', 'History', 'Political Science', 'Sociology']),
        'Administrative Assistant': ('Arts', 'Supports professional work as a administrative assistant through practical skills, analysis and domain knowledge.', ['Research', 'Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology']),
        'Executive Assistant': ('Arts', 'Supports professional work as a executive assistant through practical skills, analysis and domain knowledge.', ['Critical Thinking', 'History', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking']),
        'Customer Communications Executive': ('Arts', 'Supports professional work as a customer communications executive through practical skills, analysis and domain knowledge.', ['History', 'Political Science', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism']),
        'Fundraising Coordinator': ('Arts', 'Supports professional work as a fundraising coordinator through practical skills, analysis and domain knowledge.', ['Political Science', 'Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing']),
        'NGO Program Assistant': ('Arts', 'Supports professional work as a ngo program assistant through practical skills, analysis and domain knowledge.', ['Sociology', 'Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation']),
        'Community Outreach Coordinator': ('Arts', 'Supports professional work as a community outreach coordinator through practical skills, analysis and domain knowledge.', ['Psychology', 'Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media']),
        'Social Research Assistant': ('Arts', 'Supports professional work as a social research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Public Speaking', 'Journalism', 'Editing', 'Translation', 'Social Media']),
        'Survey Research Assistant': ('Arts', 'Supports professional work as a survey research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Journalism', 'Editing', 'Translation', 'Social Media', 'Copywriting']),
        'Public Speaking Trainer': ('Arts', 'Supports professional work as a public speaking trainer through practical skills, analysis and domain knowledge.', ['Editing', 'Translation', 'Social Media', 'Copywriting', 'English Language', 'Writing']),
        'Event Communication Executive': ('Arts', 'Supports professional work as a event communication executive through practical skills, analysis and domain knowledge.', ['Translation', 'Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication']),
        'Media Relations Assistant': ('Arts', 'Supports professional work as a media relations assistant through practical skills, analysis and domain knowledge.', ['Social Media', 'Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing']),
        'Cultural Program Coordinator': ('Arts', 'Supports professional work as a cultural program coordinator through practical skills, analysis and domain knowledge.', ['Copywriting', 'English Language', 'Writing', 'Communication', 'Creative Writing', 'Content Writing']),
        'Nurse': ('Medical / Healthcare', 'Supports professional work as a nurse through practical skills, analysis and domain knowledge.', ['Nursing Care', 'Empathy', 'Statistics', 'Research', 'Patient Care', 'First Aid']),
        'Medical Coder': ('Medical / Healthcare', 'Supports professional work as a medical coder through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Statistics', 'Research', 'Patient Care', 'First Aid', 'Anatomy']),
        'Pharmacy Assistant': ('Medical / Healthcare', 'Supports professional work as a pharmacy assistant through practical skills, analysis and domain knowledge.', ['Pharmacology', 'Research', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology']),
        'Public Health Assistant': ('Medical / Healthcare', 'Supports professional work as a public health assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology']),
        'Staff Nurse': ('Medical / Healthcare', 'Supports professional work as a staff nurse through practical skills, analysis and domain knowledge.', ['Nursing Care', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology']),
        'Clinical Nurse': ('Medical / Healthcare', 'Supports professional work as a clinical nurse through practical skills, analysis and domain knowledge.', ['Nursing Care', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills']),
        'Nursing Assistant': ('Medical / Healthcare', 'Supports professional work as a nursing assistant through practical skills, analysis and domain knowledge.', ['Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Patient Care Assistant': ('Medical / Healthcare', 'Supports professional work as a patient care assistant through practical skills, analysis and domain knowledge.', ['Patient Care', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Healthcare Assistant': ('Medical / Healthcare', 'Supports professional work as a healthcare assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding']),
        'Medical Assistant': ('Medical / Healthcare', 'Supports professional work as a medical assistant through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology']),
        'Medical Receptionist': ('Medical / Healthcare', 'Supports professional work as a medical receptionist through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology']),
        'Hospital Administrator': ('Medical / Healthcare', 'Supports professional work as a hospital administrator through practical skills, analysis and domain knowledge.', ['Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology', 'Public Health', 'Nutrition']),
        'Healthcare Administrator': ('Medical / Healthcare', 'Supports professional work as a healthcare administrator through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Coding', 'Pathology', 'Microbiology', 'Nutrition', 'Communication']),
        'Hospital Operations Executive': ('Medical / Healthcare', 'Supports professional work as a hospital operations executive through practical skills, analysis and domain knowledge.', ['Pathology', 'Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy']),
        'Clinical Coordinator': ('Medical / Healthcare', 'Supports professional work as a clinical coordinator through practical skills, analysis and domain knowledge.', ['Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'Medical Records Technician': ('Medical / Healthcare', 'Supports professional work as a medical records technician through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'Health Information Technician': ('Medical / Healthcare', 'Supports professional work as a health information technician through practical skills, analysis and domain knowledge.', ['Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research']),
        'Medical Transcriptionist': ('Medical / Healthcare', 'Supports professional work as a medical transcriptionist through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Communication', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Medical Billing Specialist': ('Medical / Healthcare', 'Supports professional work as a medical billing specialist through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Empathy', 'Statistics', 'Research', 'Patient Care', 'First Aid']),
        'Medical Coding Specialist': ('Medical / Healthcare', 'Supports professional work as a medical coding specialist through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Statistics', 'Research', 'Patient Care', 'First Aid', 'Anatomy']),
        'Medical Claims Analyst': ('Medical / Healthcare', 'Supports professional work as a medical claims analyst through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Research', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology']),
        'Healthcare Data Analyst': ('Medical / Healthcare', 'Supports professional work as a healthcare data analyst through practical skills, analysis and domain knowledge.', ['Public Health', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology']),
        'Clinical Data Coordinator': ('Medical / Healthcare', 'Supports professional work as a clinical data coordinator through practical skills, analysis and domain knowledge.', ['First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills']),
        'Clinical Research Assistant': ('Medical / Healthcare', 'Supports professional work as a clinical research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills']),
        'Clinical Research Coordinator': ('Medical / Healthcare', 'Supports professional work as a clinical research coordinator through practical skills, analysis and domain knowledge.', ['Research', 'Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care']),
        'Research Nurse': ('Medical / Healthcare', 'Supports professional work as a research nurse through practical skills, analysis and domain knowledge.', ['Research', 'Nursing Care', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Healthcare Management']),
        'Community Health Worker': ('Medical / Healthcare', 'Supports professional work as a community health worker through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding']),
        'Public Health Coordinator': ('Medical / Healthcare', 'Supports professional work as a public health coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology']),
        'Public Health Educator': ('Medical / Healthcare', 'Supports professional work as a public health educator through practical skills, analysis and domain knowledge.', ['Public Health', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology']),
        'Epidemiology Assistant': ('Medical / Healthcare', 'Supports professional work as a epidemiology assistant through practical skills, analysis and domain knowledge.', ['Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology', 'Public Health', 'Nutrition']),
        'Health Program Assistant': ('Medical / Healthcare', 'Supports professional work as a health program assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Coding', 'Pathology', 'Microbiology', 'Nutrition', 'Communication']),
        'Health Promotion Coordinator': ('Medical / Healthcare', 'Supports professional work as a health promotion coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Pathology', 'Microbiology', 'Nutrition', 'Communication', 'Empathy']),
        'Nutrition Assistant': ('Medical / Healthcare', 'Supports professional work as a nutrition assistant through practical skills, analysis and domain knowledge.', ['Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'Dietetic Assistant': ('Medical / Healthcare', 'Supports professional work as a dietetic assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research']),
        'Dietitian Assistant': ('Medical / Healthcare', 'Supports professional work as a dietitian assistant through practical skills, analysis and domain knowledge.', ['Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Pharmacy Technician': ('Medical / Healthcare', 'Supports professional work as a pharmacy technician through practical skills, analysis and domain knowledge.', ['Pharmacology', 'Communication', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Pharmacy Sales Executive': ('Medical / Healthcare', 'Supports professional work as a pharmacy sales executive through practical skills, analysis and domain knowledge.', ['Communication', 'Pharmacology', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Pharmaceutical Sales Representative': ('Medical / Healthcare', 'Supports professional work as a pharmaceutical sales representative through practical skills, analysis and domain knowledge.', ['Communication', 'Statistics', 'Research', 'Patient Care', 'First Aid', 'Anatomy']),
        'Medical Representative': ('Medical / Healthcare', 'Supports professional work as a medical representative through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Research', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology']),
        'Pharmaceutical Marketing Executive': ('Medical / Healthcare', 'Supports professional work as a pharmaceutical marketing executive through practical skills, analysis and domain knowledge.', ['Patient Care', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology']),
        'Medical Laboratory Technician': ('Medical / Healthcare', 'Supports professional work as a medical laboratory technician through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Clinical Skills']),
        'Pathology Lab Technician': ('Medical / Healthcare', 'Supports professional work as a pathology lab technician through practical skills, analysis and domain knowledge.', ['Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care']),
        'Microbiology Lab Technician': ('Medical / Healthcare', 'Supports professional work as a microbiology lab technician through practical skills, analysis and domain knowledge.', ['Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Biochemistry Lab Technician': ('Medical / Healthcare', 'Supports professional work as a biochemistry lab technician through practical skills, analysis and domain knowledge.', ['Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding']),
        'Radiology Assistant': ('Medical / Healthcare', 'Supports professional work as a radiology assistant through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology']),
        'Radiology Technician': ('Medical / Healthcare', 'Supports professional work as a radiology technician through practical skills, analysis and domain knowledge.', ['Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology']),
        'Dental Assistant': ('Medical / Healthcare', 'Supports professional work as a dental assistant through practical skills, analysis and domain knowledge.', ['Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology', 'Public Health']),
        'Dental Receptionist': ('Medical / Healthcare', 'Supports professional work as a dental receptionist through practical skills, analysis and domain knowledge.', ['Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology', 'Public Health', 'Nutrition']),
        'Physiotherapy Assistant': ('Medical / Healthcare', 'Supports professional work as a physiotherapy assistant through practical skills, analysis and domain knowledge.', ['Medical Coding', 'Pathology', 'Microbiology', 'Public Health', 'Nutrition', 'Communication']),
        'Occupational Therapy Assistant': ('Medical / Healthcare', 'Supports professional work as a occupational therapy assistant through practical skills, analysis and domain knowledge.', ['Pathology', 'Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy']),
        'Optometry Assistant': ('Medical / Healthcare', 'Supports professional work as a optometry assistant through practical skills, analysis and domain knowledge.', ['Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'Emergency Medical Technician': ('Medical / Healthcare', 'Supports professional work as a emergency medical technician through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'First Aid Trainer': ('Medical / Healthcare', 'Supports professional work as a first aid trainer through practical skills, analysis and domain knowledge.', ['Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Healthcare Customer Service Executive': ('Medical / Healthcare', 'Supports professional work as a healthcare customer service executive through practical skills, analysis and domain knowledge.', ['Public Health', 'Communication', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Hospital Front Desk Executive': ('Medical / Healthcare', 'Supports professional work as a hospital front desk executive through practical skills, analysis and domain knowledge.', ['Empathy', 'Statistics', 'Research', 'Patient Care', 'First Aid', 'Anatomy']),
        'Medical Office Assistant': ('Medical / Healthcare', 'Supports professional work as a medical office assistant through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Statistics', 'Research', 'Patient Care', 'First Aid', 'Anatomy']),
        'Healthcare Quality Analyst': ('Medical / Healthcare', 'Supports professional work as a healthcare quality analyst through practical skills, analysis and domain knowledge.', ['Public Health', 'Research', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology']),
        'Hospital Quality Coordinator': ('Medical / Healthcare', 'Supports professional work as a hospital quality coordinator through practical skills, analysis and domain knowledge.', ['Patient Care', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology']),
        'Infection Control Assistant': ('Medical / Healthcare', 'Supports professional work as a infection control assistant through practical skills, analysis and domain knowledge.', ['First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills']),
        'Medical Safety Coordinator': ('Medical / Healthcare', 'Supports professional work as a medical safety coordinator through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Anatomy', 'Physiology', 'Pharmacology', 'Clinical Skills', 'Nursing Care']),
        'Clinical Documentation Specialist': ('Medical / Healthcare', 'Supports professional work as a clinical documentation specialist through practical skills, analysis and domain knowledge.', ['Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Healthcare Compliance Assistant': ('Medical / Healthcare', 'Supports professional work as a healthcare compliance assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Healthcare HR Coordinator': ('Medical / Healthcare', 'Supports professional work as a healthcare hr coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding']),
        'Medical Equipment Coordinator': ('Medical / Healthcare', 'Supports professional work as a medical equipment coordinator through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology']),
        'Biomedical Equipment Assistant': ('Medical / Healthcare', 'Supports professional work as a biomedical equipment assistant through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology']),
        'Healthcare Procurement Executive': ('Medical / Healthcare', 'Supports professional work as a healthcare procurement executive through practical skills, analysis and domain knowledge.', ['Public Health', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology', 'Nutrition']),
        'Healthcare Supply Chain Assistant': ('Medical / Healthcare', 'Supports professional work as a healthcare supply chain assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Coding', 'Pathology', 'Microbiology', 'Nutrition', 'Communication']),
        'Health Insurance Executive': ('Medical / Healthcare', 'Supports professional work as a health insurance executive through practical skills, analysis and domain knowledge.', ['Public Health', 'Pathology', 'Microbiology', 'Nutrition', 'Communication', 'Empathy']),
        'Health Insurance Claims Assistant': ('Medical / Healthcare', 'Supports professional work as a health insurance claims assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Microbiology', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'Medical Underwriting Assistant': ('Medical / Healthcare', 'Supports professional work as a medical underwriting assistant through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics']),
        'Telehealth Coordinator': ('Medical / Healthcare', 'Supports professional work as a telehealth coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research']),
        'Patient Relationship Executive': ('Medical / Healthcare', 'Supports professional work as a patient relationship executive through practical skills, analysis and domain knowledge.', ['Patient Care', 'Communication', 'Empathy', 'Statistics', 'Research', 'First Aid']),
        'Patient Support Coordinator': ('Medical / Healthcare', 'Supports professional work as a patient support coordinator through practical skills, analysis and domain knowledge.', ['Patient Care', 'Empathy', 'Statistics', 'Research', 'First Aid', 'Anatomy']),
        'Care Coordinator': ('Medical / Healthcare', 'Supports professional work as a care coordinator through practical skills, analysis and domain knowledge.', ['Statistics', 'Research', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology']),
        'Home Healthcare Coordinator': ('Medical / Healthcare', 'Supports professional work as a home healthcare coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Research', 'Patient Care', 'First Aid', 'Anatomy', 'Physiology']),
        'Wellness Program Coordinator': ('Medical / Healthcare', 'Supports professional work as a wellness program coordinator through practical skills, analysis and domain knowledge.', ['Patient Care', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology']),
        'Mental Health Program Assistant': ('Medical / Healthcare', 'Supports professional work as a mental health program assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'First Aid', 'Anatomy', 'Physiology', 'Pharmacology', 'Medical Terminology']),
        'Medical Research Assistant': ('Medical / Healthcare', 'Supports professional work as a medical research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Medical Terminology', 'Anatomy', 'Physiology', 'Pharmacology', 'Clinical Skills']),
        'Clinical Trial Assistant': ('Medical / Healthcare', 'Supports professional work as a clinical trial assistant through practical skills, analysis and domain knowledge.', ['Physiology', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Healthcare Project Coordinator': ('Medical / Healthcare', 'Supports professional work as a healthcare project coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Pharmacology', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management']),
        'Health Informatics Assistant': ('Medical / Healthcare', 'Supports professional work as a health informatics assistant through practical skills, analysis and domain knowledge.', ['Public Health', 'Medical Terminology', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding']),
        'Health Informatics Analyst': ('Medical / Healthcare', 'Supports professional work as a health informatics analyst through practical skills, analysis and domain knowledge.', ['Public Health', 'Clinical Skills', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology']),
        'Medical Data Entry Operator': ('Medical / Healthcare', 'Supports professional work as a medical data entry operator through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Nursing Care', 'Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology']),
        'Pharmacovigilance Assistant': ('Medical / Healthcare', 'Supports professional work as a pharmacovigilance assistant through practical skills, analysis and domain knowledge.', ['Healthcare Management', 'Medical Coding', 'Pathology', 'Microbiology', 'Public Health', 'Nutrition']),
        'Drug Safety Associate': ('Medical / Healthcare', 'Supports professional work as a drug safety associate through practical skills, analysis and domain knowledge.', ['Medical Coding', 'Pathology', 'Microbiology', 'Public Health', 'Nutrition', 'Communication']),
        'Regulatory Affairs Assistant': ('Medical / Healthcare', 'Supports professional work as a regulatory affairs assistant through practical skills, analysis and domain knowledge.', ['Pathology', 'Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy']),
        'Medical Affairs Assistant': ('Medical / Healthcare', 'Supports professional work as a medical affairs assistant through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Microbiology', 'Public Health', 'Nutrition', 'Communication', 'Empathy']),
        'Hospital Training Coordinator': ('Medical / Healthcare', 'Supports professional work as a hospital training coordinator through practical skills, analysis and domain knowledge.', ['Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research']),
        'Healthcare Operations Analyst': ('Medical / Healthcare', 'Supports professional work as a healthcare operations analyst through practical skills, analysis and domain knowledge.', ['Public Health', 'Nutrition', 'Communication', 'Empathy', 'Statistics', 'Research']),
        'Healthcare Business Analyst': ('Medical / Healthcare', 'Supports professional work as a healthcare business analyst through practical skills, analysis and domain knowledge.', ['Public Health', 'Communication', 'Empathy', 'Statistics', 'Research', 'Patient Care']),
        'Medical Content Writer': ('Medical / Healthcare', 'Supports professional work as a medical content writer through practical skills, analysis and domain knowledge.', ['Medical Terminology', 'Empathy', 'Statistics', 'Research', 'Patient Care', 'First Aid']),
        'Laboratory Technician': ('Science', 'Supports professional work as a laboratory technician through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Mathematics', 'Statistics', 'Scientific Writing', 'Data Analysis', 'Research Methodology']),
        'Research Assistant': ('Science', 'Supports professional work as a research assistant through practical skills, analysis and domain knowledge.', ['Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design']),
        'Biotechnologist': ('Science', 'Supports professional work as a biotechnologist through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science']),
        'Data Science Intern': ('Science', 'Supports professional work as a data science intern through practical skills, analysis and domain knowledge.', ['Data Analysis', 'Scientific Writing', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Lab Assistant': ('Science', 'Supports professional work as a lab assistant through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Research Scientist': ('Science', 'Supports professional work as a research scientist through practical skills, analysis and domain knowledge.', ['Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation']),
        'Junior Scientist': ('Science', 'Supports professional work as a junior scientist through practical skills, analysis and domain knowledge.', ['Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python']),
        'Scientific Officer': ('Science', 'Supports professional work as a scientific officer through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Laboratory Analyst': ('Science', 'Supports professional work as a laboratory analyst through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Quality Control Analyst': ('Science', 'Supports professional work as a quality control analyst through practical skills, analysis and domain knowledge.', ['Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy', 'Biology']),
        'Quality Assurance Analyst': ('Science', 'Supports professional work as a quality assurance analyst through practical skills, analysis and domain knowledge.', ['Observation', 'Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry']),
        'Chemistry Lab Assistant': ('Science', 'Supports professional work as a chemistry lab assistant through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Chemistry', 'Python', 'Pandas', 'NumPy', 'Biology']),
        'Biology Lab Assistant': ('Science', 'Supports professional work as a biology lab assistant through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Biology', 'Pandas', 'NumPy', 'Chemistry', 'Physics']),
        'Physics Lab Assistant': ('Science', 'Supports professional work as a physics lab assistant through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Physics', 'NumPy', 'Biology', 'Chemistry', 'Mathematics']),
        'Microbiology Technician': ('Science', 'Supports professional work as a microbiology technician through practical skills, analysis and domain knowledge.', ['Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills']),
        'Biochemistry Technician': ('Science', 'Supports professional work as a biochemistry technician through practical skills, analysis and domain knowledge.', ['Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing']),
        'Molecular Biology Assistant': ('Science', 'Supports professional work as a molecular biology assistant through practical skills, analysis and domain knowledge.', ['Biology', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing']),
        'Genetics Research Assistant': ('Science', 'Supports professional work as a genetics research assistant through practical skills, analysis and domain knowledge.', ['Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology']),
        'Biotechnology Research Assistant': ('Science', 'Supports professional work as a biotechnology research assistant through practical skills, analysis and domain knowledge.', ['Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design']),
        'Bioinformatics Analyst': ('Science', 'Supports professional work as a bioinformatics analyst through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science']),
        'Bioinformatics Assistant': ('Science', 'Supports professional work as a bioinformatics assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Computational Biology Assistant': ('Science', 'Supports professional work as a computational biology assistant through practical skills, analysis and domain knowledge.', ['Biology', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Environmental Scientist': ('Science', 'Supports professional work as a environmental scientist through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Research Methodology', 'Experiment Design', 'Biotechnology', 'Quality Control', 'Observation']),
        'Environmental Analyst': ('Science', 'Supports professional work as a environmental analyst through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Experiment Design', 'Biotechnology', 'Quality Control', 'Observation', 'Python']),
        'Environmental Research Assistant': ('Science', 'Supports professional work as a environmental research assistant through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Ecology Research Assistant': ('Science', 'Supports professional work as a ecology research assistant through practical skills, analysis and domain knowledge.', ['Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'Conservation Assistant': ('Science', 'Supports professional work as a conservation assistant through practical skills, analysis and domain knowledge.', ['Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy', 'Biology']),
        'Geology Assistant': ('Science', 'Supports professional work as a geology assistant through practical skills, analysis and domain knowledge.', ['Observation', 'Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry']),
        'Geologist': ('Science', 'Supports professional work as a geologist through practical skills, analysis and domain knowledge.', ['Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry', 'Physics']),
        'Geophysics Assistant': ('Science', 'Supports professional work as a geophysics assistant through practical skills, analysis and domain knowledge.', ['Physics', 'Pandas', 'NumPy', 'Biology', 'Chemistry', 'Mathematics']),
        'Meteorology Assistant': ('Science', 'Supports professional work as a meteorology assistant through practical skills, analysis and domain knowledge.', ['NumPy', 'Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics']),
        'Climate Research Assistant': ('Science', 'Supports professional work as a climate research assistant through practical skills, analysis and domain knowledge.', ['Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills']),
        'Oceanography Assistant': ('Science', 'Supports professional work as a oceanography assistant through practical skills, analysis and domain knowledge.', ['Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing']),
        'Marine Biology Assistant': ('Science', 'Supports professional work as a marine biology assistant through practical skills, analysis and domain knowledge.', ['Biology', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing']),
        'Food Science Assistant': ('Science', 'Supports professional work as a food science assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Data Analysis', 'Research Methodology']),
        'Food Technologist': ('Science', 'Supports professional work as a food technologist through practical skills, analysis and domain knowledge.', ['Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design']),
        'Food Quality Analyst': ('Science', 'Supports professional work as a food quality analyst through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science']),
        'Pharmaceutical Research Assistant': ('Science', 'Supports professional work as a pharmaceutical research assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Chemical Analyst': ('Science', 'Supports professional work as a chemical analyst through practical skills, analysis and domain knowledge.', ['Chemistry', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Chemistry Research Assistant': ('Science', 'Supports professional work as a chemistry research assistant through practical skills, analysis and domain knowledge.', ['Chemistry', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control']),
        'Physics Research Assistant': ('Science', 'Supports professional work as a physics research assistant through practical skills, analysis and domain knowledge.', ['Physics', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation']),
        'Mathematics Research Assistant': ('Science', 'Supports professional work as a mathematics research assistant through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Statistics Assistant': ('Science', 'Supports professional work as a statistics assistant through practical skills, analysis and domain knowledge.', ['Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'Statistician': ('Science', 'Supports professional work as a statistician through practical skills, analysis and domain knowledge.', ['Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy', 'Biology']),
        'Data Analyst - Science': ('Science', 'Supports professional work as a data analyst - science through practical skills, analysis and domain knowledge.', ['Data Analysis', 'Scientific Writing', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'Scientific Data Analyst': ('Science', 'Supports professional work as a scientific data analyst through practical skills, analysis and domain knowledge.', ['Data Analysis', 'Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry']),
        'Research Data Coordinator': ('Science', 'Supports professional work as a research data coordinator through practical skills, analysis and domain knowledge.', ['Data Analysis', 'Pandas', 'NumPy', 'Biology', 'Chemistry', 'Physics']),
        'Scientific Programmer': ('Science', 'Supports professional work as a scientific programmer through practical skills, analysis and domain knowledge.', ['NumPy', 'Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics']),
        'Scientific Computing Assistant': ('Science', 'Supports professional work as a scientific computing assistant through practical skills, analysis and domain knowledge.', ['Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills']),
        'Experimental Scientist': ('Science', 'Supports professional work as a experimental scientist through practical skills, analysis and domain knowledge.', ['Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing']),
        'Experiment Technician': ('Science', 'Supports professional work as a experiment technician through practical skills, analysis and domain knowledge.', ['Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis']),
        'Clinical Laboratory Assistant': ('Science', 'Supports professional work as a clinical laboratory assistant through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Mathematics', 'Statistics', 'Scientific Writing', 'Data Analysis', 'Research Methodology']),
        'Quality Control Technician': ('Science', 'Supports professional work as a quality control technician through practical skills, analysis and domain knowledge.', ['Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design']),
        'Quality Assurance Technician': ('Science', 'Supports professional work as a quality assurance technician through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science']),
        'Process Analyst - Science': ('Science', 'Supports professional work as a process analyst - science through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Environmental Health Assistant': ('Science', 'Supports professional work as a environmental health assistant through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Biotechnology', 'Quality Control']),
        'Water Quality Analyst': ('Science', 'Supports professional work as a water quality analyst through practical skills, analysis and domain knowledge.', ['Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation']),
        'Air Quality Analyst': ('Science', 'Supports professional work as a air quality analyst through practical skills, analysis and domain knowledge.', ['Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python']),
        'Sustainability Analyst': ('Science', 'Supports professional work as a sustainability analyst through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Sustainability Coordinator': ('Science', 'Supports professional work as a sustainability coordinator through practical skills, analysis and domain knowledge.', ['Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'Renewable Energy Research Assistant': ('Science', 'Supports professional work as a renewable energy research assistant through practical skills, analysis and domain knowledge.', ['Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy', 'Biology']),
        'Energy Analyst': ('Science', 'Supports professional work as a energy analyst through practical skills, analysis and domain knowledge.', ['Observation', 'Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry']),
        'Materials Science Assistant': ('Science', 'Supports professional work as a materials science assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry']),
        'Materials Research Assistant': ('Science', 'Supports professional work as a materials research assistant through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Biology', 'Chemistry', 'Physics', 'Mathematics']),
        'Nanotechnology Research Assistant': ('Science', 'Supports professional work as a nanotechnology research assistant through practical skills, analysis and domain knowledge.', ['NumPy', 'Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics']),
        'Forensic Science Assistant': ('Science', 'Supports professional work as a forensic science assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics']),
        'Forensic Lab Technician': ('Science', 'Supports professional work as a forensic lab technician through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Scientific Writing']),
        'Forensic Analyst': ('Science', 'Supports professional work as a forensic analyst through practical skills, analysis and domain knowledge.', ['Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis']),
        'Scientific Documentation Assistant': ('Science', 'Supports professional work as a scientific documentation assistant through practical skills, analysis and domain knowledge.', ['Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology']),
        'Scientific Technical Writer': ('Science', 'Supports professional work as a scientific technical writer through practical skills, analysis and domain knowledge.', ['Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design']),
        'Science Communicator': ('Science', 'Supports professional work as a science communicator through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Laboratory Skills', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science']),
        'Science Content Writer': ('Science', 'Supports professional work as a science content writer through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Science Teacher Assistant': ('Science', 'Supports professional work as a science teacher assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'STEM Program Coordinator': ('Science', 'Supports professional work as a stem program coordinator through practical skills, analysis and domain knowledge.', ['Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation']),
        'Science Project Coordinator': ('Science', 'Supports professional work as a science project coordinator through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation']),
        'Research Coordinator': ('Science', 'Supports professional work as a research coordinator through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Research Operations Assistant': ('Science', 'Supports professional work as a research operations assistant through practical skills, analysis and domain knowledge.', ['Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'Laboratory Coordinator': ('Science', 'Supports professional work as a laboratory coordinator through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'Lab Quality Coordinator': ('Science', 'Supports professional work as a lab quality coordinator through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Observation', 'Python', 'Pandas', 'NumPy', 'Biology']),
        'Scientific Procurement Assistant': ('Science', 'Supports professional work as a scientific procurement assistant through practical skills, analysis and domain knowledge.', ['Python', 'Pandas', 'NumPy', 'Biology', 'Chemistry', 'Physics']),
        'Research Administration Assistant': ('Science', 'Supports professional work as a research administration assistant through practical skills, analysis and domain knowledge.', ['Pandas', 'NumPy', 'Biology', 'Chemistry', 'Physics', 'Mathematics']),
        'Data Collection Assistant': ('Science', 'Supports professional work as a data collection assistant through practical skills, analysis and domain knowledge.', ['Data Analysis', 'NumPy', 'Biology', 'Chemistry', 'Physics', 'Mathematics']),
        'Field Research Assistant': ('Science', 'Supports professional work as a field research assistant through practical skills, analysis and domain knowledge.', ['Biology', 'Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills']),
        'Field Technician': ('Science', 'Supports professional work as a field technician through practical skills, analysis and domain knowledge.', ['Chemistry', 'Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing']),
        'Survey Technician': ('Science', 'Supports professional work as a survey technician through practical skills, analysis and domain knowledge.', ['Physics', 'Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis']),
        'Geospatial Research Assistant': ('Science', 'Supports professional work as a geospatial research assistant through practical skills, analysis and domain knowledge.', ['Mathematics', 'Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology']),
        'GIS Analyst': ('Science', 'Supports professional work as a gis analyst through practical skills, analysis and domain knowledge.', ['Statistics', 'Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design']),
        'Remote Sensing Assistant': ('Science', 'Supports professional work as a remote sensing assistant through practical skills, analysis and domain knowledge.', ['Laboratory Skills', 'Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science']),
        'Agricultural Research Assistant': ('Science', 'Supports professional work as a agricultural research assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology']),
        'Agriculture Scientist Assistant': ('Science', 'Supports professional work as a agriculture scientist assistant through practical skills, analysis and domain knowledge.', ['Data Analysis', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control']),
        'Plant Science Assistant': ('Science', 'Supports professional work as a plant science assistant through practical skills, analysis and domain knowledge.', ['Scientific Writing', 'Research Methodology', 'Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control']),
        'Botany Research Assistant': ('Science', 'Supports professional work as a botany research assistant through practical skills, analysis and domain knowledge.', ['Experiment Design', 'Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python']),
        'Zoology Research Assistant': ('Science', 'Supports professional work as a zoology research assistant through practical skills, analysis and domain knowledge.', ['Environmental Science', 'Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas']),
        'Wildlife Research Assistant': ('Science', 'Supports professional work as a wildlife research assistant through practical skills, analysis and domain knowledge.', ['Biotechnology', 'Quality Control', 'Observation', 'Python', 'Pandas', 'NumPy']),
        'School Teacher': ('Education', 'Supports professional work as a school teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Research', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'Online Tutor': ('Education', 'Supports professional work as a online tutor through practical skills, analysis and domain knowledge.', ['Teaching', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment']),
        'Curriculum Assistant': ('Education', 'Supports professional work as a curriculum assistant through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Teaching', 'Lesson Planning', 'Classroom Management', 'Assessment', 'Child Development']),
        'Education Counselor': ('Education', 'Supports professional work as a education counselor through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Primary Teacher': ('Education', 'Supports professional work as a primary teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'Secondary Teacher': ('Education', 'Supports professional work as a secondary teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills']),
        'High School Teacher': ('Education', 'Supports professional work as a high school teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'College Lecturer': ('Education', 'Supports professional work as a college lecturer through practical skills, analysis and domain knowledge.', ['Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'Assistant Professor': ('Education', 'Supports professional work as a assistant professor through practical skills, analysis and domain knowledge.', ['Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Teaching Assistant': ('Education', 'Supports professional work as a teaching assistant through practical skills, analysis and domain knowledge.', ['Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research']),
        'Academic Coordinator': ('Education', 'Supports professional work as a academic coordinator through practical skills, analysis and domain knowledge.', ['Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Academic Counselor': ('Education', 'Supports professional work as a academic counselor through practical skills, analysis and domain knowledge.', ['Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching']),
        'Career Counselor': ('Education', 'Supports professional work as a career counselor through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning']),
        'Student Counselor': ('Education', 'Supports professional work as a student counselor through practical skills, analysis and domain knowledge.', ['Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management']),
        'School Counselor': ('Education', 'Supports professional work as a school counselor through practical skills, analysis and domain knowledge.', ['Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'Special Education Assistant': ('Education', 'Supports professional work as a special education assistant through practical skills, analysis and domain knowledge.', ['Teaching', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment']),
        'Special Educator': ('Education', 'Supports professional work as a special educator through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Montessori Teacher': ('Education', 'Supports professional work as a montessori teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Preschool Teacher': ('Education', 'Supports professional work as a preschool teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'Kindergarten Teacher': ('Education', 'Supports professional work as a kindergarten teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills']),
        'Early Childhood Educator': ('Education', 'Supports professional work as a early childhood educator through practical skills, analysis and domain knowledge.', ['Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching']),
        'Subject Matter Expert': ('Education', 'Supports professional work as a subject matter expert through practical skills, analysis and domain knowledge.', ['Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'Subject Teacher - Mathematics': ('Education', 'Supports professional work as a subject teacher - mathematics through practical skills, analysis and domain knowledge.', ['Teaching', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'Subject Teacher - Science': ('Education', 'Supports professional work as a subject teacher - science through practical skills, analysis and domain knowledge.', ['Teaching', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Subject Teacher - English': ('Education', 'Supports professional work as a subject teacher - english through practical skills, analysis and domain knowledge.', ['Teaching', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research']),
        'Subject Teacher - Computer Science': ('Education', 'Supports professional work as a subject teacher - computer science through practical skills, analysis and domain knowledge.', ['Teaching', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Subject Teacher - Commerce': ('Education', 'Supports professional work as a subject teacher - commerce through practical skills, analysis and domain knowledge.', ['Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Lesson Planning']),
        'Subject Teacher - History': ('Education', 'Supports professional work as a subject teacher - history through practical skills, analysis and domain knowledge.', ['Teaching', 'Leadership', 'Research', 'Public Speaking', 'Lesson Planning', 'Classroom Management']),
        'Subject Teacher - Geography': ('Education', 'Supports professional work as a subject teacher - geography through practical skills, analysis and domain knowledge.', ['Teaching', 'Research', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'Language Teacher': ('Education', 'Supports professional work as a language teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment']),
        'English Teacher': ('Education', 'Supports professional work as a english teacher through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'English Tutor': ('Education', 'Supports professional work as a english tutor through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Math Tutor': ('Education', 'Supports professional work as a math tutor through practical skills, analysis and domain knowledge.', ['Teaching', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'Science Tutor': ('Education', 'Supports professional work as a science tutor through practical skills, analysis and domain knowledge.', ['Teaching', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills']),
        'Computer Tutor': ('Education', 'Supports professional work as a computer tutor through practical skills, analysis and domain knowledge.', ['Teaching', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Coding Instructor': ('Education', 'Supports professional work as a coding instructor through practical skills, analysis and domain knowledge.', ['Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'STEM Educator': ('Education', 'Supports professional work as a stem educator through practical skills, analysis and domain knowledge.', ['Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Online Course Instructor': ('Education', 'Supports professional work as a online course instructor through practical skills, analysis and domain knowledge.', ['Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research']),
        'E-Learning Instructor': ('Education', 'Supports professional work as a e-learning instructor through practical skills, analysis and domain knowledge.', ['Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Instructional Designer': ('Education', 'Supports professional work as a instructional designer through practical skills, analysis and domain knowledge.', ['Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching']),
        'Learning Designer': ('Education', 'Supports professional work as a learning designer through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning']),
        'Curriculum Developer': ('Education', 'Supports professional work as a curriculum developer through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning']),
        'Curriculum Designer': ('Education', 'Supports professional work as a curriculum designer through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management']),
        'Assessment Coordinator': ('Education', 'Supports professional work as a assessment coordinator through practical skills, analysis and domain knowledge.', ['Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment']),
        'Examination Coordinator': ('Education', 'Supports professional work as a examination coordinator through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Academic Content Developer': ('Education', 'Supports professional work as a academic content developer through practical skills, analysis and domain knowledge.', ['Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'Educational Content Writer': ('Education', 'Supports professional work as a educational content writer through practical skills, analysis and domain knowledge.', ['Teaching', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'E-Learning Content Developer': ('Education', 'Supports professional work as a e-learning content developer through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Education Program Coordinator': ('Education', 'Supports professional work as a education program coordinator through practical skills, analysis and domain knowledge.', ['Teaching', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Education Project Coordinator': ('Education', 'Supports professional work as a education project coordinator through practical skills, analysis and domain knowledge.', ['Teaching', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching']),
        'School Administrator': ('Education', 'Supports professional work as a school administrator through practical skills, analysis and domain knowledge.', ['Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'College Administrator': ('Education', 'Supports professional work as a college administrator through practical skills, analysis and domain knowledge.', ['Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research']),
        'Admissions Counselor': ('Education', 'Supports professional work as a admissions counselor through practical skills, analysis and domain knowledge.', ['Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Admissions Coordinator': ('Education', 'Supports professional work as a admissions coordinator through practical skills, analysis and domain knowledge.', ['Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching']),
        'Student Affairs Coordinator': ('Education', 'Supports professional work as a student affairs coordinator through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning']),
        'Student Support Coordinator': ('Education', 'Supports professional work as a student support coordinator through practical skills, analysis and domain knowledge.', ['Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management']),
        'Library Assistant - Education': ('Education', 'Supports professional work as a library assistant - education through practical skills, analysis and domain knowledge.', ['Teaching', 'Research', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'Education Research Assistant': ('Education', 'Supports professional work as a education research assistant through practical skills, analysis and domain knowledge.', ['Research', 'Teaching', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'Education Data Analyst': ('Education', 'Supports professional work as a education data analyst through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Education Policy Assistant': ('Education', 'Supports professional work as a education policy assistant through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Education NGO Coordinator': ('Education', 'Supports professional work as a education ngo coordinator through practical skills, analysis and domain knowledge.', ['Teaching', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'Training Executive': ('Education', 'Supports professional work as a training executive through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Corporate Trainer': ('Education', 'Supports professional work as a corporate trainer through practical skills, analysis and domain knowledge.', ['Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching']),
        'Soft Skills Trainer': ('Education', 'Supports professional work as a soft skills trainer through practical skills, analysis and domain knowledge.', ['Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'Communication Trainer': ('Education', 'Supports professional work as a communication trainer through practical skills, analysis and domain knowledge.', ['Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Teacher Trainer': ('Education', 'Supports professional work as a teacher trainer through practical skills, analysis and domain knowledge.', ['Teaching', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Faculty Development Coordinator': ('Education', 'Supports professional work as a faculty development coordinator through practical skills, analysis and domain knowledge.', ['Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Learning and Development Executive': ('Education', 'Supports professional work as a learning and development executive through practical skills, analysis and domain knowledge.', ['Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching']),
        'Training Content Developer': ('Education', 'Supports professional work as a training content developer through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning']),
        'Training Coordinator': ('Education', 'Supports professional work as a training coordinator through practical skills, analysis and domain knowledge.', ['Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management']),
        'Education Technology Specialist': ('Education', 'Supports professional work as a education technology specialist through practical skills, analysis and domain knowledge.', ['Teaching', 'Research', 'Public Speaking', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'EdTech Support Specialist': ('Education', 'Supports professional work as a edtech support specialist through practical skills, analysis and domain knowledge.', ['Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment']),
        'LMS Administrator': ('Education', 'Supports professional work as a lms administrator through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'LMS Support Executive': ('Education', 'Supports professional work as a lms support executive through practical skills, analysis and domain knowledge.', ['Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology']),
        'Online Learning Coordinator': ('Education', 'Supports professional work as a online learning coordinator through practical skills, analysis and domain knowledge.', ['Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills']),
        'Instructional Technology Assistant': ('Education', 'Supports professional work as a instructional technology assistant through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Assessment Designer': ('Education', 'Supports professional work as a assessment designer through practical skills, analysis and domain knowledge.', ['Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching']),
        'Question Paper Developer': ('Education', 'Supports professional work as a question paper developer through practical skills, analysis and domain knowledge.', ['Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'Academic Editor': ('Education', 'Supports professional work as a academic editor through practical skills, analysis and domain knowledge.', ['Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Textbook Editor': ('Education', 'Supports professional work as a textbook editor through practical skills, analysis and domain knowledge.', ['Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research']),
        'Publishing Coordinator - Education': ('Education', 'Supports professional work as a publishing coordinator - education through practical skills, analysis and domain knowledge.', ['Teaching', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research']),
        'Educational Psychologist Assistant': ('Education', 'Supports professional work as a educational psychologist assistant through practical skills, analysis and domain knowledge.', ['Teaching', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Child Development Assistant': ('Education', 'Supports professional work as a child development assistant through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning']),
        'Childcare Educator': ('Education', 'Supports professional work as a childcare educator through practical skills, analysis and domain knowledge.', ['Leadership', 'Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management']),
        'Youth Program Coordinator': ('Education', 'Supports professional work as a youth program coordinator through practical skills, analysis and domain knowledge.', ['Research', 'Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design']),
        'After-School Program Coordinator': ('Education', 'Supports professional work as a after-school program coordinator through practical skills, analysis and domain knowledge.', ['Public Speaking', 'Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment']),
        'Community Education Coordinator': ('Education', 'Supports professional work as a community education coordinator through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Adult Education Instructor': ('Education', 'Supports professional work as a adult education instructor through practical skills, analysis and domain knowledge.', ['Teaching', 'Lesson Planning', 'Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development']),
        'Vocational Trainer': ('Education', 'Supports professional work as a vocational trainer through practical skills, analysis and domain knowledge.', ['Classroom Management', 'Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills']),
        'Skill Development Trainer': ('Education', 'Supports professional work as a skill development trainer through practical skills, analysis and domain knowledge.', ['Curriculum Design', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Education Consultant': ('Education', 'Supports professional work as a education consultant through practical skills, analysis and domain knowledge.', ['Teaching', 'Assessment', 'Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge']),
        'Academic Operations Executive': ('Education', 'Supports professional work as a academic operations executive through practical skills, analysis and domain knowledge.', ['Child Development', 'Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills']),
        'School Operations Coordinator': ('Education', 'Supports professional work as a school operations coordinator through practical skills, analysis and domain knowledge.', ['Educational Psychology', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Education Program Assistant': ('Education', 'Supports professional work as a education program assistant through practical skills, analysis and domain knowledge.', ['Teaching', 'Presentation Skills', 'Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership']),
        'Exam Support Executive': ('Education', 'Supports professional work as a exam support executive through practical skills, analysis and domain knowledge.', ['Subject Knowledge', 'Online Teaching', 'Communication Skills', 'Leadership', 'Research', 'Public Speaking']),
        'Graphic Designer': ('Design & Media', 'Supports professional work as a graphic designer through practical skills, analysis and domain knowledge.', ['Graphic Design', 'UI/UX', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Video Editor': ('Design & Media', 'Supports professional work as a video editor through practical skills, analysis and domain knowledge.', ['Video Editing', 'Photography', 'Animation', 'Typography', 'Branding', 'Storytelling']),
        'Digital Marketing Executive': ('Design & Media', 'Supports professional work as a digital marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Animation', 'Typography', 'Branding', 'Storytelling', 'SEO']),
        'Content Creator': ('Design & Media', 'Supports professional work as a content creator through practical skills, analysis and domain knowledge.', ['Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'UI Designer': ('Design & Media', 'Supports professional work as a ui designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'UX Designer': ('Design & Media', 'Supports professional work as a ux designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Product Designer': ('Design & Media', 'Supports professional work as a product designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting']),
        'Visual Designer': ('Design & Media', 'Supports professional work as a visual designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'SEO', 'Content Creation', 'Social Media', 'Copywriting', 'Graphic Design']),
        'Web Designer': ('Design & Media', 'Supports professional work as a web designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Content Creation', 'Social Media', 'Copywriting', 'Graphic Design', 'Adobe Photoshop']),
        'Interaction Designer': ('Design & Media', 'Supports professional work as a interaction designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Social Media', 'Copywriting', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator']),
        'Communication Designer': ('Design & Media', 'Supports professional work as a communication designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Copywriting', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma']),
        'Brand Designer': ('Design & Media', 'Supports professional work as a brand designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Logo Designer': ('Design & Media', 'Supports professional work as a logo designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Illustrator': ('Design & Media', 'Supports professional work as a illustrator through practical skills, analysis and domain knowledge.', ['Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography']),
        'Digital Illustrator': ('Design & Media', 'Supports professional work as a digital illustrator through practical skills, analysis and domain knowledge.', ['Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography', 'Animation']),
        'Art Director Assistant': ('Design & Media', 'Supports professional work as a art director assistant through practical skills, analysis and domain knowledge.', ['Figma', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Creative Designer': ('Design & Media', 'Supports professional work as a creative designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Motion Graphics Designer': ('Design & Media', 'Supports professional work as a motion graphics designer through practical skills, analysis and domain knowledge.', ['Graphic Design', 'UI/UX', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Motion Designer': ('Design & Media', 'Supports professional work as a motion designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Photography', 'Animation', 'Typography', 'Branding', 'Storytelling']),
        '2D Animator': ('Design & Media', 'Supports professional work as a 2d animator through practical skills, analysis and domain knowledge.', ['Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO']),
        '3D Animator': ('Design & Media', 'Supports professional work as a 3d animator through practical skills, analysis and domain knowledge.', ['Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO']),
        '3D Artist': ('Design & Media', 'Supports professional work as a 3d artist through practical skills, analysis and domain knowledge.', ['Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        '3D Modeler': ('Design & Media', 'Supports professional work as a 3d modeler through practical skills, analysis and domain knowledge.', ['Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting']),
        'Character Designer': ('Design & Media', 'Supports professional work as a character designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting']),
        'Game Artist': ('Design & Media', 'Supports professional work as a game artist through practical skills, analysis and domain knowledge.', ['SEO', 'Content Creation', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design']),
        'Game UI Designer': ('Design & Media', 'Supports professional work as a game ui designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Content Creation', 'Social Media', 'Copywriting', 'Graphic Design', 'Adobe Photoshop']),
        'Game Animator': ('Design & Media', 'Supports professional work as a game animator through practical skills, analysis and domain knowledge.', ['Animation', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop']),
        'Storyboard Artist': ('Design & Media', 'Supports professional work as a storyboard artist through practical skills, analysis and domain knowledge.', ['Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma']),
        'Concept Artist': ('Design & Media', 'Supports professional work as a concept artist through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Photographer': ('Design & Media', 'Supports professional work as a photographer through practical skills, analysis and domain knowledge.', ['Photography', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Photo Editor': ('Design & Media', 'Supports professional work as a photo editor through practical skills, analysis and domain knowledge.', ['Photography', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing']),
        'Photo Retoucher': ('Design & Media', 'Supports professional work as a photo retoucher through practical skills, analysis and domain knowledge.', ['Photography', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Animation']),
        'Videographer': ('Design & Media', 'Supports professional work as a videographer through practical skills, analysis and domain knowledge.', ['Video Editing', 'Figma', 'Canva', 'Photography', 'Animation', 'Typography']),
        'Cinematographer Assistant': ('Design & Media', 'Supports professional work as a cinematographer assistant through practical skills, analysis and domain knowledge.', ['Canva', 'Video Editing', 'Photography', 'Animation', 'Typography', 'Branding']),
        'Video Producer': ('Design & Media', 'Supports professional work as a video producer through practical skills, analysis and domain knowledge.', ['Video Editing', 'Photography', 'Animation', 'Typography', 'Branding', 'Storytelling']),
        'Production Assistant': ('Design & Media', 'Supports professional work as a production assistant through practical skills, analysis and domain knowledge.', ['Photography', 'Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing']),
        'Film Editor': ('Design & Media', 'Supports professional work as a film editor through practical skills, analysis and domain knowledge.', ['Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO']),
        'Film Production Coordinator': ('Design & Media', 'Supports professional work as a film production coordinator through practical skills, analysis and domain knowledge.', ['Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'YouTube Content Creator': ('Design & Media', 'Supports professional work as a youtube content creator through practical skills, analysis and domain knowledge.', ['Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Social Media Content Creator': ('Design & Media', 'Supports professional work as a social media content creator through practical skills, analysis and domain knowledge.', ['Social Media', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Copywriting']),
        'Influencer Marketing Coordinator': ('Design & Media', 'Supports professional work as a influencer marketing coordinator through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting', 'UI/UX']),
        'Social Media Designer': ('Design & Media', 'Supports professional work as a social media designer through practical skills, analysis and domain knowledge.', ['Social Media', 'UI/UX', 'SEO', 'Content Creation', 'Copywriting', 'Graphic Design']),
        'Social Media Strategist': ('Design & Media', 'Supports professional work as a social media strategist through practical skills, analysis and domain knowledge.', ['Social Media', 'Content Creation', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop']),
        'Digital Marketing Specialist': ('Design & Media', 'Supports professional work as a digital marketing specialist through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop']),
        'SEO Specialist': ('Design & Media', 'Supports professional work as a seo specialist through practical skills, analysis and domain knowledge.', ['SEO', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator']),
        'SEO Content Strategist': ('Design & Media', 'Supports professional work as a seo content strategist through practical skills, analysis and domain knowledge.', ['SEO', 'UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma']),
        'SEM Executive': ('Design & Media', 'Supports professional work as a sem executive through practical skills, analysis and domain knowledge.', ['Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing']),
        'Email Marketing Executive': ('Design & Media', 'Supports professional work as a email marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing']),
        'Marketing Content Designer': ('Design & Media', 'Supports professional work as a marketing content designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Digital Marketing', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing']),
        'Content Marketing Executive': ('Design & Media', 'Supports professional work as a content marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Figma', 'Canva', 'Video Editing', 'Photography', 'Animation']),
        'Brand Marketing Executive': ('Design & Media', 'Supports professional work as a brand marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Marketing Coordinator': ('Design & Media', 'Supports professional work as a marketing coordinator through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Video Editing', 'Photography', 'Animation', 'Typography', 'Branding']),
        'Campaign Coordinator': ('Design & Media', 'Supports professional work as a campaign coordinator through practical skills, analysis and domain knowledge.', ['Photography', 'Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing']),
        'Advertising Coordinator': ('Design & Media', 'Supports professional work as a advertising coordinator through practical skills, analysis and domain knowledge.', ['Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO']),
        'Creative Copywriter': ('Design & Media', 'Supports professional work as a creative copywriter through practical skills, analysis and domain knowledge.', ['Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'Art Copywriter': ('Design & Media', 'Supports professional work as a art copywriter through practical skills, analysis and domain knowledge.', ['Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Presentation Designer': ('Design & Media', 'Supports professional work as a presentation designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Infographic Designer': ('Design & Media', 'Supports professional work as a infographic designer through practical skills, analysis and domain knowledge.', ['Graphic Design', 'UI/UX', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Data Visualization Designer': ('Design & Media', 'Supports professional work as a data visualization designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'SEO', 'Content Creation', 'Social Media', 'Copywriting', 'Graphic Design']),
        'Publication Designer': ('Design & Media', 'Supports professional work as a publication designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Content Creation', 'Social Media', 'Copywriting', 'Graphic Design', 'Adobe Photoshop']),
        'Print Designer': ('Design & Media', 'Supports professional work as a print designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Social Media', 'Copywriting', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator']),
        'Packaging Designer': ('Design & Media', 'Supports professional work as a packaging designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Copywriting', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma']),
        'Fashion Graphic Designer': ('Design & Media', 'Supports professional work as a fashion graphic designer through practical skills, analysis and domain knowledge.', ['Graphic Design', 'UI/UX', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Textile Designer': ('Design & Media', 'Supports professional work as a textile designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Interior Design Assistant': ('Design & Media', 'Supports professional work as a interior design assistant through practical skills, analysis and domain knowledge.', ['UI/UX', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing']),
        'Interior Designer': ('Design & Media', 'Supports professional work as a interior designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography']),
        'Landscape Design Assistant': ('Design & Media', 'Supports professional work as a landscape design assistant through practical skills, analysis and domain knowledge.', ['UI/UX', 'Figma', 'Canva', 'Video Editing', 'Photography', 'Animation']),
        'Event Designer': ('Design & Media', 'Supports professional work as a event designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Exhibition Designer': ('Design & Media', 'Supports professional work as a exhibition designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Video Editing', 'Photography', 'Animation', 'Typography', 'Branding']),
        'Set Designer': ('Design & Media', 'Supports professional work as a set designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Photography', 'Animation', 'Typography', 'Branding', 'Storytelling']),
        'Production Designer': ('Design & Media', 'Supports professional work as a production designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing']),
        'Sound Editor': ('Design & Media', 'Supports professional work as a sound editor through practical skills, analysis and domain knowledge.', ['Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'Audio Editor': ('Design & Media', 'Supports professional work as a audio editor through practical skills, analysis and domain knowledge.', ['Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Podcast Producer': ('Design & Media', 'Supports professional work as a podcast producer through practical skills, analysis and domain knowledge.', ['Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting']),
        'Podcast Editor': ('Design & Media', 'Supports professional work as a podcast editor through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting', 'UI/UX']),
        'Radio Content Producer': ('Design & Media', 'Supports professional work as a radio content producer through practical skills, analysis and domain knowledge.', ['SEO', 'Content Creation', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design']),
        'Broadcast Producer': ('Design & Media', 'Supports professional work as a broadcast producer through practical skills, analysis and domain knowledge.', ['Content Creation', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop']),
        'Media Coordinator': ('Design & Media', 'Supports professional work as a media coordinator through practical skills, analysis and domain knowledge.', ['Social Media', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator']),
        'Media Planner': ('Design & Media', 'Supports professional work as a media planner through practical skills, analysis and domain knowledge.', ['Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma']),
        'Media Buyer Assistant': ('Design & Media', 'Supports professional work as a media buyer assistant through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Public Relations Designer': ('Design & Media', 'Supports professional work as a public relations designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Digital Media Executive': ('Design & Media', 'Supports professional work as a digital media executive through practical skills, analysis and domain knowledge.', ['Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography']),
        'Multimedia Designer': ('Design & Media', 'Supports professional work as a multimedia designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography']),
        'Multimedia Developer': ('Design & Media', 'Supports professional work as a multimedia developer through practical skills, analysis and domain knowledge.', ['Figma', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Instructional Media Designer': ('Design & Media', 'Supports professional work as a instructional media designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'E-Learning Designer': ('Design & Media', 'Supports professional work as a e-learning designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Video Editing', 'Photography', 'Animation', 'Typography', 'Branding']),
        'Creative Producer': ('Design & Media', 'Supports professional work as a creative producer through practical skills, analysis and domain knowledge.', ['Photography', 'Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing']),
        'Content Production Coordinator': ('Design & Media', 'Supports professional work as a content production coordinator through practical skills, analysis and domain knowledge.', ['Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO']),
        'Studio Assistant': ('Design & Media', 'Supports professional work as a studio assistant through practical skills, analysis and domain knowledge.', ['Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'Photography Assistant': ('Design & Media', 'Supports professional work as a photography assistant through practical skills, analysis and domain knowledge.', ['Photography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'Design Researcher': ('Design & Media', 'Supports professional work as a design researcher through practical skills, analysis and domain knowledge.', ['UI/UX', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media']),
        'Design Strategist': ('Design & Media', 'Supports professional work as a design strategist through practical skills, analysis and domain knowledge.', ['UI/UX', 'Digital Marketing', 'SEO', 'Content Creation', 'Social Media', 'Copywriting']),
        'Creative Project Coordinator': ('Design & Media', 'Supports professional work as a creative project coordinator through practical skills, analysis and domain knowledge.', ['SEO', 'Content Creation', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design']),
        'Brand Strategist': ('Design & Media', 'Supports professional work as a brand strategist through practical skills, analysis and domain knowledge.', ['Content Creation', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop']),
        'Marketing Analyst': ('Design & Media', 'Supports professional work as a marketing analyst through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Social Media', 'Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop']),
        'Digital Analytics Executive': ('Design & Media', 'Supports professional work as a digital analytics executive through practical skills, analysis and domain knowledge.', ['Copywriting', 'UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma']),
        'Content Operations Executive': ('Design & Media', 'Supports professional work as a content operations executive through practical skills, analysis and domain knowledge.', ['UI/UX', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'SEO Analyst': ('Design & Media', 'Supports professional work as a seo analyst through practical skills, analysis and domain knowledge.', ['SEO', 'Graphic Design', 'Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva']),
        'Web Content Manager': ('Design & Media', 'Supports professional work as a web content manager through practical skills, analysis and domain knowledge.', ['Adobe Photoshop', 'Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography']),
        'Community Content Manager': ('Design & Media', 'Supports professional work as a community content manager through practical skills, analysis and domain knowledge.', ['Adobe Illustrator', 'Figma', 'Canva', 'Video Editing', 'Photography', 'Animation']),
        'Creative Technology Assistant': ('Design & Media', 'Supports professional work as a creative technology assistant through practical skills, analysis and domain knowledge.', ['Figma', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Accessibility Designer': ('Design & Media', 'Supports professional work as a accessibility designer through practical skills, analysis and domain knowledge.', ['UI/UX', 'Canva', 'Video Editing', 'Photography', 'Animation', 'Typography']),
        'Design QA Specialist': ('Design & Media', 'Supports professional work as a design qa specialist through practical skills, analysis and domain knowledge.', ['UI/UX', 'Video Editing', 'Photography', 'Animation', 'Typography', 'Branding']),
        'Visual Content Specialist': ('Design & Media', 'Supports professional work as a visual content specialist through practical skills, analysis and domain knowledge.', ['Photography', 'Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing']),
        'Media Production Assistant': ('Design & Media', 'Supports professional work as a media production assistant through practical skills, analysis and domain knowledge.', ['Animation', 'Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO']),
        'Creative Operations Coordinator': ('Design & Media', 'Supports professional work as a creative operations coordinator through practical skills, analysis and domain knowledge.', ['Typography', 'Branding', 'Storytelling', 'Digital Marketing', 'SEO', 'Content Creation']),
        'Hotel Front Office Executive': ('Hospitality & Travel', 'Supports professional work as a hotel front office executive through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Front Office', 'Excel', 'Customer Service', 'Food & Beverage', 'Housekeeping']),
        'Event Coordinator': ('Hospitality & Travel', 'Supports professional work as a event coordinator through practical skills, analysis and domain knowledge.', ['Event Planning', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping']),
        'Travel Consultant': ('Hospitality & Travel', 'Supports professional work as a travel consultant through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Hotel Operations Assistant': ('Hospitality & Travel', 'Supports professional work as a hotel operations assistant through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Front Office Executive': ('Hospitality & Travel', 'Supports professional work as a front office executive through practical skills, analysis and domain knowledge.', ['Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Guest Relations Executive': ('Hospitality & Travel', 'Supports professional work as a guest relations executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Guest Service Associate': ('Hospitality & Travel', 'Supports professional work as a guest service associate through practical skills, analysis and domain knowledge.', ['Customer Service', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Receptionist - Hotel': ('Hospitality & Travel', 'Supports professional work as a receptionist - hotel through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Reservations Executive': ('Hospitality & Travel', 'Supports professional work as a reservations executive through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Reservations Agent': ('Hospitality & Travel', 'Supports professional work as a reservations agent through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Concierge Assistant': ('Hospitality & Travel', 'Supports professional work as a concierge assistant through practical skills, analysis and domain knowledge.', ['Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Bell Desk Associate': ('Hospitality & Travel', 'Supports professional work as a bell desk associate through practical skills, analysis and domain knowledge.', ['Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Housekeeping Supervisor': ('Hospitality & Travel', 'Supports professional work as a housekeeping supervisor through practical skills, analysis and domain knowledge.', ['Housekeeping', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Housekeeping Executive': ('Hospitality & Travel', 'Supports professional work as a housekeeping executive through practical skills, analysis and domain knowledge.', ['Housekeeping', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations']),
        'Housekeeping Assistant': ('Hospitality & Travel', 'Supports professional work as a housekeeping assistant through practical skills, analysis and domain knowledge.', ['Housekeeping', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage']),
        'Room Attendant': ('Hospitality & Travel', 'Supports professional work as a room attendant through practical skills, analysis and domain knowledge.', ['Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping']),
        'Laundry Supervisor': ('Hospitality & Travel', 'Supports professional work as a laundry supervisor through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Laundry Associate': ('Hospitality & Travel', 'Supports professional work as a laundry associate through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Food and Beverage Executive': ('Hospitality & Travel', 'Supports professional work as a food and beverage executive through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism']),
        'Restaurant Supervisor': ('Hospitality & Travel', 'Supports professional work as a restaurant supervisor through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism']),
        'Restaurant Captain': ('Hospitality & Travel', 'Supports professional work as a restaurant captain through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Restaurant Host': ('Hospitality & Travel', 'Supports professional work as a restaurant host through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Restaurant Manager Assistant': ('Hospitality & Travel', 'Supports professional work as a restaurant manager assistant through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Food Service Associate': ('Hospitality & Travel', 'Supports professional work as a food service associate through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management']),
        'Banquet Executive': ('Hospitality & Travel', 'Supports professional work as a banquet executive through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Banquet Coordinator': ('Hospitality & Travel', 'Supports professional work as a banquet coordinator through practical skills, analysis and domain knowledge.', ['Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Catering Coordinator': ('Hospitality & Travel', 'Supports professional work as a catering coordinator through practical skills, analysis and domain knowledge.', ['Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Catering Assistant': ('Hospitality & Travel', 'Supports professional work as a catering assistant through practical skills, analysis and domain knowledge.', ['Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations']),
        'Kitchen Assistant': ('Hospitality & Travel', 'Supports professional work as a kitchen assistant through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage']),
        'Commis Chef': ('Hospitality & Travel', 'Supports professional work as a commis chef through practical skills, analysis and domain knowledge.', ['Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office']),
        'Chef Assistant': ('Hospitality & Travel', 'Supports professional work as a chef assistant through practical skills, analysis and domain knowledge.', ['Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping']),
        'Bakery Assistant': ('Hospitality & Travel', 'Supports professional work as a bakery assistant through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Pastry Assistant': ('Hospitality & Travel', 'Supports professional work as a pastry assistant through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Barista': ('Hospitality & Travel', 'Supports professional work as a barista through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism']),
        'Cafe Supervisor': ('Hospitality & Travel', 'Supports professional work as a cafe supervisor through practical skills, analysis and domain knowledge.', ['Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Restaurant Cashier': ('Hospitality & Travel', 'Supports professional work as a restaurant cashier through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Hotel Sales Executive': ('Hospitality & Travel', 'Supports professional work as a hotel sales executive through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Hotel Marketing Executive': ('Hospitality & Travel', 'Supports professional work as a hotel marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Hotel Operations', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Revenue Management Assistant': ('Hospitality & Travel', 'Supports professional work as a revenue management assistant through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Revenue Analyst - Hotel': ('Hospitality & Travel', 'Supports professional work as a revenue analyst - hotel through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Yield Management Assistant': ('Hospitality & Travel', 'Supports professional work as a yield management assistant through practical skills, analysis and domain knowledge.', ['Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Hotel Accounts Executive': ('Hospitality & Travel', 'Supports professional work as a hotel accounts executive through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Hotel HR Assistant': ('Hospitality & Travel', 'Supports professional work as a hotel hr assistant through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Hotel Training Coordinator': ('Hospitality & Travel', 'Supports professional work as a hotel training coordinator through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Food & Beverage']),
        'Hotel Procurement Executive': ('Hospitality & Travel', 'Supports professional work as a hotel procurement executive through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Social Media', 'Excel', 'Customer Service', 'Food & Beverage', 'Front Office']),
        'Hotel Purchase Assistant': ('Hospitality & Travel', 'Supports professional work as a hotel purchase assistant through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Excel', 'Customer Service', 'Food & Beverage', 'Front Office', 'Housekeeping']),
        'Hotel Stores Assistant': ('Hospitality & Travel', 'Supports professional work as a hotel stores assistant through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Customer Service', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Hotel Maintenance Coordinator': ('Hospitality & Travel', 'Supports professional work as a hotel maintenance coordinator through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Hotel Operations Coordinator': ('Hospitality & Travel', 'Supports professional work as a hotel operations coordinator through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Hotel Duty Manager': ('Hospitality & Travel', 'Supports professional work as a hotel duty manager through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism']),
        'Guest Experience Coordinator': ('Hospitality & Travel', 'Supports professional work as a guest experience coordinator through practical skills, analysis and domain knowledge.', ['Customer Service', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Customer Service Executive': ('Hospitality & Travel', 'Supports professional work as a customer service executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Customer Experience Executive': ('Hospitality & Travel', 'Supports professional work as a customer experience executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Travel Agent': ('Hospitality & Travel', 'Supports professional work as a travel agent through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management']),
        'Travel Consultant Assistant': ('Hospitality & Travel', 'Supports professional work as a travel consultant assistant through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Travel Coordinator': ('Hospitality & Travel', 'Supports professional work as a travel coordinator through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Tour Coordinator': ('Hospitality & Travel', 'Supports professional work as a tour coordinator through practical skills, analysis and domain knowledge.', ['Tourism', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Tour Operator Assistant': ('Hospitality & Travel', 'Supports professional work as a tour operator assistant through practical skills, analysis and domain knowledge.', ['Tourism', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Tour Guide': ('Hospitality & Travel', 'Supports professional work as a tour guide through practical skills, analysis and domain knowledge.', ['Tourism', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations']),
        'Tourism Executive': ('Hospitality & Travel', 'Supports professional work as a tourism executive through practical skills, analysis and domain knowledge.', ['Tourism', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage']),
        'Tourism Coordinator': ('Hospitality & Travel', 'Supports professional work as a tourism coordinator through practical skills, analysis and domain knowledge.', ['Tourism', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office']),
        'Tourism Marketing Executive': ('Hospitality & Travel', 'Supports professional work as a tourism marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Tourism', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office']),
        'Destination Management Assistant': ('Hospitality & Travel', 'Supports professional work as a destination management assistant through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Travel Operations Executive': ('Hospitality & Travel', 'Supports professional work as a travel operations executive through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Tourism']),
        'Ticketing Executive': ('Hospitality & Travel', 'Supports professional work as a ticketing executive through practical skills, analysis and domain knowledge.', ['Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Airline Customer Service Executive': ('Hospitality & Travel', 'Supports professional work as a airline customer service executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Airport Ground Staff': ('Hospitality & Travel', 'Supports professional work as a airport ground staff through practical skills, analysis and domain knowledge.', ['Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Airport Operations Assistant': ('Hospitality & Travel', 'Supports professional work as a airport operations assistant through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management']),
        'Flight Operations Assistant': ('Hospitality & Travel', 'Supports professional work as a flight operations assistant through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Travel Desk Executive': ('Hospitality & Travel', 'Supports professional work as a travel desk executive through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Corporate Travel Coordinator': ('Hospitality & Travel', 'Supports professional work as a corporate travel coordinator through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Visa Documentation Assistant': ('Hospitality & Travel', 'Supports professional work as a visa documentation assistant through practical skills, analysis and domain knowledge.', ['Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Travel Documentation Executive': ('Hospitality & Travel', 'Supports professional work as a travel documentation executive through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Cruise Operations Assistant': ('Hospitality & Travel', 'Supports professional work as a cruise operations assistant through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage']),
        'Resort Operations Executive': ('Hospitality & Travel', 'Supports professional work as a resort operations executive through practical skills, analysis and domain knowledge.', ['Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office']),
        'Resort Front Office Executive': ('Hospitality & Travel', 'Supports professional work as a resort front office executive through practical skills, analysis and domain knowledge.', ['Front Office', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Housekeeping']),
        'Resort Guest Relations Executive': ('Hospitality & Travel', 'Supports professional work as a resort guest relations executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Resort Activities Coordinator': ('Hospitality & Travel', 'Supports professional work as a resort activities coordinator through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Event Planner': ('Hospitality & Travel', 'Supports professional work as a event planner through practical skills, analysis and domain knowledge.', ['Event Planning', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Travel Planning', 'Tourism']),
        'Event Manager Assistant': ('Hospitality & Travel', 'Supports professional work as a event manager assistant through practical skills, analysis and domain knowledge.', ['Event Planning', 'Front Office', 'Housekeeping', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Wedding Planner Assistant': ('Hospitality & Travel', 'Supports professional work as a wedding planner assistant through practical skills, analysis and domain knowledge.', ['Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Wedding Coordinator': ('Hospitality & Travel', 'Supports professional work as a wedding coordinator through practical skills, analysis and domain knowledge.', ['Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Conference Coordinator': ('Hospitality & Travel', 'Supports professional work as a conference coordinator through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management']),
        'MICE Coordinator': ('Hospitality & Travel', 'Supports professional work as a mice coordinator through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Exhibition Coordinator': ('Hospitality & Travel', 'Supports professional work as a exhibition coordinator through practical skills, analysis and domain knowledge.', ['Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Hospitality Sales Executive': ('Hospitality & Travel', 'Supports professional work as a hospitality sales executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Hospitality Customer Service Executive': ('Hospitality & Travel', 'Supports professional work as a hospitality customer service executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Food Safety Assistant': ('Hospitality & Travel', 'Supports professional work as a food safety assistant through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Food Quality Coordinator': ('Hospitality & Travel', 'Supports professional work as a food quality coordinator through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations']),
        'Restaurant Operations Executive': ('Hospitality & Travel', 'Supports professional work as a restaurant operations executive through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Front Office']),
        'Restaurant Marketing Executive': ('Hospitality & Travel', 'Supports professional work as a restaurant marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Food & Beverage', 'Excel', 'Customer Service', 'Hotel Operations', 'Front Office']),
        'Restaurant Inventory Assistant': ('Hospitality & Travel', 'Supports professional work as a restaurant inventory assistant through practical skills, analysis and domain knowledge.', ['Food & Beverage', 'Customer Service', 'Hotel Operations', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Hospitality Data Analyst': ('Hospitality & Travel', 'Supports professional work as a hospitality data analyst through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Hotel Business Development Executive': ('Hospitality & Travel', 'Supports professional work as a hotel business development executive through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Tourism Business Development Executive': ('Hospitality & Travel', 'Supports professional work as a tourism business development executive through practical skills, analysis and domain knowledge.', ['Tourism', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Communication Skills']),
        'Hospitality Business Analyst': ('Hospitality & Travel', 'Supports professional work as a hospitality business analyst through practical skills, analysis and domain knowledge.', ['Customer Service', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Travel Business Analyst': ('Hospitality & Travel', 'Supports professional work as a travel business analyst through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Event Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Guest Feedback Coordinator': ('Hospitality & Travel', 'Supports professional work as a guest feedback coordinator through practical skills, analysis and domain knowledge.', ['Customer Service', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Service Quality Executive': ('Hospitality & Travel', 'Supports professional work as a service quality executive through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Hospitality Training Executive': ('Hospitality & Travel', 'Supports professional work as a hospitality training executive through practical skills, analysis and domain knowledge.', ['Customer Service', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Tourism Research Assistant': ('Hospitality & Travel', 'Supports professional work as a tourism research assistant through practical skills, analysis and domain knowledge.', ['Tourism', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media']),
        'Travel Content Writer': ('Hospitality & Travel', 'Supports professional work as a travel content writer through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Destination Content Creator': ('Hospitality & Travel', 'Supports professional work as a destination content creator through practical skills, analysis and domain knowledge.', ['Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations']),
        'Hospitality Social Media Executive': ('Hospitality & Travel', 'Supports professional work as a hospitality social media executive through practical skills, analysis and domain knowledge.', ['Social Media', 'Customer Service', 'Digital Marketing', 'Excel', 'Hotel Operations', 'Food & Beverage']),
        'Travel Social Media Executive': ('Hospitality & Travel', 'Supports professional work as a travel social media executive through practical skills, analysis and domain knowledge.', ['Social Media', 'Travel Planning', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage']),
        'Hotel Digital Marketing Executive': ('Hospitality & Travel', 'Supports professional work as a hotel digital marketing executive through practical skills, analysis and domain knowledge.', ['Digital Marketing', 'Hotel Operations', 'Excel', 'Customer Service', 'Food & Beverage', 'Front Office']),
        'Hospitality Operations Analyst': ('Hospitality & Travel', 'Supports professional work as a hospitality operations analyst through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Hospitality Administrative Assistant': ('Hospitality & Travel', 'Supports professional work as a hospitality administrative assistant through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Event Operations Assistant': ('Hospitality & Travel', 'Supports professional work as a event operations assistant through practical skills, analysis and domain knowledge.', ['Event Planning', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Travel Planning', 'Tourism']),
        'Event Logistics Coordinator': ('Hospitality & Travel', 'Supports professional work as a event logistics coordinator through practical skills, analysis and domain knowledge.', ['Event Planning', 'Front Office', 'Housekeeping', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Venue Coordinator': ('Hospitality & Travel', 'Supports professional work as a venue coordinator through practical skills, analysis and domain knowledge.', ['Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Banquet Sales Coordinator': ('Hospitality & Travel', 'Supports professional work as a banquet sales coordinator through practical skills, analysis and domain knowledge.', ['Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
        'Catering Sales Executive': ('Hospitality & Travel', 'Supports professional work as a catering sales executive through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management']),
        'Resort Activities Assistant': ('Hospitality & Travel', 'Supports professional work as a resort activities assistant through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Tourist Information Officer': ('Hospitality & Travel', 'Supports professional work as a tourist information officer through practical skills, analysis and domain knowledge.', ['Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing']),
        'Visitor Services Assistant': ('Hospitality & Travel', 'Supports professional work as a visitor services assistant through practical skills, analysis and domain knowledge.', ['Teamwork', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Heritage Tourism Coordinator': ('Hospitality & Travel', 'Supports professional work as a heritage tourism coordinator through practical skills, analysis and domain knowledge.', ['Tourism', 'Problem Solving', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel']),
        'Adventure Tourism Coordinator': ('Hospitality & Travel', 'Supports professional work as a adventure tourism coordinator through practical skills, analysis and domain knowledge.', ['Tourism', 'Time Management', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service']),
        'Travel Experience Designer': ('Hospitality & Travel', 'Supports professional work as a travel experience designer through practical skills, analysis and domain knowledge.', ['Travel Planning', 'Digital Marketing', 'Social Media', 'Excel', 'Customer Service', 'Hotel Operations']),
        'Itinerary Planner': ('Hospitality & Travel', 'Supports professional work as a itinerary planner through practical skills, analysis and domain knowledge.', ['Social Media', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office']),
        'Corporate Events Assistant': ('Hospitality & Travel', 'Supports professional work as a corporate events assistant through practical skills, analysis and domain knowledge.', ['Event Planning', 'Excel', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office']),
        'Event Registration Coordinator': ('Hospitality & Travel', 'Supports professional work as a event registration coordinator through practical skills, analysis and domain knowledge.', ['Event Planning', 'Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping']),
        'Hospitality Procurement Analyst': ('Hospitality & Travel', 'Supports professional work as a hospitality procurement analyst through practical skills, analysis and domain knowledge.', ['Customer Service', 'Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning']),
        'Hotel Revenue Coordinator': ('Hospitality & Travel', 'Supports professional work as a hotel revenue coordinator through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Food & Beverage', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning']),
        'Hotel Reservations Supervisor': ('Hospitality & Travel', 'Supports professional work as a hotel reservations supervisor through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism']),
        'Front Office Supervisor': ('Hospitality & Travel', 'Supports professional work as a front office supervisor through practical skills, analysis and domain knowledge.', ['Front Office', 'Housekeeping', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills']),
        'Guest Services Supervisor': ('Hospitality & Travel', 'Supports professional work as a guest services supervisor through practical skills, analysis and domain knowledge.', ['Customer Service', 'Event Planning', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork']),
        'Hotel Quality Coordinator': ('Hospitality & Travel', 'Supports professional work as a hotel quality coordinator through practical skills, analysis and domain knowledge.', ['Hotel Operations', 'Travel Planning', 'Tourism', 'Communication Skills', 'Teamwork', 'Problem Solving']),
    }
    # Some roles intentionally reference broad soft skills. Add them if they
    # were not already present in the dictionary above.
    extra = {
        "Writing":"Arts", "Attention to Detail":"Medical", "Analytics":"Design & Media",
        "Teamwork":"Hospitality & Travel", "Problem Solving":"Hospitality & Travel",
        "Time Management":"Hospitality & Travel"
    }
    for name, cat in extra.items():
        conn.execute("INSERT OR IGNORE INTO skills(skill_name, category) VALUES (?,?)", (name,cat))

    for role, (domain, desc, reqs) in roles.items():
        conn.execute("INSERT OR IGNORE INTO job_roles(role_name,description,domain) VALUES (?,?,?)", (role,desc,domain))
        rid = conn.execute("SELECT id FROM job_roles WHERE role_name=?", (role,)).fetchone()["id"]
        conn.execute("UPDATE job_roles SET domain=?, description=? WHERE id=?", (domain,desc,rid))
        for i, skill in enumerate(reqs):
            sid = conn.execute("SELECT id FROM skills WHERE skill_name=?", (skill,)).fetchone()
            if sid:
                conn.execute("INSERT OR IGNORE INTO job_role_skills(job_role_id,skill_id,importance) VALUES (?,?,?)",
                             (rid,sid["id"],5 if i < 3 else 3))

    resources = {
        "HTML":("HTML Basics","Learn page structure, semantic tags and forms.","https://developer.mozilla.org/en-US/docs/Web/HTML","Beginner"),
        "CSS":("CSS Basics","Learn layouts, responsive design and styling.","https://developer.mozilla.org/en-US/docs/Web/CSS","Beginner"),
        "JavaScript":("JavaScript Guide","Learn variables, functions, DOM and modern JavaScript.","https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide","Beginner"),
        "React":("React Learn","Learn components, state, props and hooks.","https://react.dev/learn","Intermediate"),
        "Git":("Git Learn","Learn version control, commits, branches and collaboration.","https://git-scm.com/doc","Beginner"),
        "Python":("Python Tutorial","Learn Python syntax, functions and modules.","https://docs.python.org/3/tutorial/","Beginner"),
        "SQL":("SQL Tutorial","Learn queries, filtering, joins and aggregation.","https://www.w3schools.com/sql/","Beginner"),
        "Excel":("Excel Training","Learn spreadsheets, formulas and data analysis.","https://support.microsoft.com/excel","Beginner"),
        "Power BI":("Power BI Learning","Learn dashboards, reports and data visualization.","https://learn.microsoft.com/power-bi/","Beginner"),
        "Machine Learning":("Scikit-learn Tutorials","Practice common machine learning workflows.","https://scikit-learn.org/stable/getting_started.html","Intermediate")
    }
    for skill, (title, desc, url, diff) in resources.items():
        sid = conn.execute("SELECT id FROM skills WHERE skill_name=?", (skill,)).fetchone()
        if sid:
            conn.execute("INSERT OR IGNORE INTO learning_resources (skill_id,title,description,url,difficulty) VALUES (?,?,?,?,?)",
                         (sid["id"],title,desc,url,diff))

def login_required():
    return "user_id" in session

@app.context_processor
def inject_user():
    user = None
    if "user_id" in session:
        conn=db()
        user=conn.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
        conn.close()
    return {"current_user": user}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form["name"].strip()
        email=request.form["email"].strip().lower()
        password=request.form["password"]
        confirm=request.form["confirm"]
        if not name or not email or not password:
            flash("Please fill all required fields.","danger")
        elif password != confirm:
            flash("Passwords do not match.","danger")
        else:
            conn=db()
            try:
                stream=request.form.get("education_stream","").strip()
                conn.execute("INSERT INTO users(name,email,password,education_stream,created_at) VALUES (?,?,?,?,?)",
                             (name,email,generate_password_hash(password),stream,datetime.now().isoformat(timespec="seconds")))
                conn.commit()
                flash("Registration successful. Please login.","success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Email already registered.","danger")
            finally: conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form["email"].strip().lower()
        password=request.form["password"]
        conn=db(); user=conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone(); conn.close()
        if user and check_password_hash(user["password"],password):
            session["user_id"]=user["id"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.","danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    student_stream=get_student_stream(conn, session["user_id"])
    skills=conn.execute("""SELECT s.skill_name FROM student_skills ss JOIN skills s ON s.id=ss.skill_id
                           WHERE ss.user_id=? ORDER BY s.skill_name""",(session["user_id"],)).fetchall()
    analyses=conn.execute("""SELECT a.*, r.role_name FROM analyses a JOIN job_roles r ON r.id=a.job_role_id
                             WHERE a.user_id=? ORDER BY a.id DESC LIMIT 5""",(session["user_id"],)).fetchall()
    latest=conn.execute("""SELECT a.*, r.role_name FROM analyses a JOIN job_roles r ON r.id=a.job_role_id
                          WHERE a.user_id=? ORDER BY a.id DESC LIMIT 1""",(session["user_id"],)).fetchone()
    progress_row=conn.execute("""SELECT COALESCE(AVG(percentage),0) AS avg_progress,
                                       COALESCE(SUM(completed),0) AS completed_count
                                FROM progress WHERE user_id=?""",(session["user_id"],)).fetchone()

    # Calculate the 10 job roles that match the student's current skills best.
    roles=conn.execute("""SELECT r.id, r.role_name, r.description, r.domain, GROUP_CONCAT(s.skill_name) reqs
                          FROM job_roles r JOIN job_role_skills j ON j.job_role_id=r.id
                          JOIN skills s ON s.id=j.skill_id
                          GROUP BY r.id""").fetchall()
    conn.close()

    top_matches=sorted(
        role_recommendations([r["skill_name"] for r in skills], roles),
        key=lambda x: (-x["match_percent"], -x["matched"], x["role_name"])
    )
    if student_stream:
        allowed=set(STREAM_DOMAINS.get(student_stream, []))
        role_domains={r["role_name"]: r["domain"] for r in roles}
        same=[r for r in top_matches if role_domains.get(r["role_name"]) in allowed]
        if len(same) >= 3:
            top_matches=same
    top_matches=top_matches[:10]

    return render_template("dashboard.html", skills=skills, analyses=analyses, latest=latest,
                           avg_progress=round(progress_row["avg_progress"]),
                           completed_count=progress_row["completed_count"],
                           student_stream=student_stream,
                           top_matches=top_matches)

@app.route("/resume", methods=["GET","POST"])
def resume():
    if not login_required(): return redirect(url_for("login"))
    if request.method=="POST":
        f=request.files.get("resume")
        if not f or not f.filename.lower().endswith(".pdf"):
            flash("Please upload a PDF resume.","danger")
            return redirect(url_for("resume"))
        filename=secure_filename(f.filename)
        path=os.path.join(app.config["UPLOAD_FOLDER"], filename)
        f.save(path)
        try:
            text=extract_text_from_pdf(path)
            found=extract_skills_from_text(text)
            conn=db()
            for skill, confidence in found:
                row=conn.execute("SELECT id FROM skills WHERE skill_name=?",(skill,)).fetchone()
                if row:
                    conn.execute("""INSERT OR REPLACE INTO student_skills(user_id,skill_id,source,confidence)
                                    VALUES (?,?,?,?)""",(session["user_id"],row["id"],"Resume",confidence))
                    conn.execute("""INSERT OR IGNORE INTO progress(user_id,skill_id,percentage,completed,updated_at)
                                    VALUES (?,?,?,?,?)""",(session["user_id"],row["id"],50,0,datetime.now().isoformat(timespec="seconds")))
            conn.commit(); conn.close()
            if found:
                flash(f"Resume analyzed. Detected {len(found)} skill(s): " + ", ".join(s for s,_ in found), "success")
            else:
                flash("No supported skills were detected. You can add skills manually from My Skills.","warning")
        except Exception as e:
            flash("Could not read the PDF. Try a text-based PDF.","danger")
    return render_template("resume.html")

@app.route("/skills", methods=["GET","POST"])
def skills_page():
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    if request.method=="POST":
        selected=request.form.getlist("skills")
        conn.execute("DELETE FROM student_skills WHERE user_id=? AND source='Manual'",(session["user_id"],))
        for name in selected:
            row=conn.execute("SELECT id FROM skills WHERE skill_name=?",(name,)).fetchone()
            if row:
                conn.execute("INSERT OR IGNORE INTO student_skills(user_id,skill_id,source,confidence) VALUES (?,?,?,?)",
                             (session["user_id"],row["id"],"Manual",1.0))
        conn.commit()
        flash("Your skills were updated.","success")
        conn.close()
        return redirect(url_for("skills_page"))
    all_skills=conn.execute("SELECT * FROM skills ORDER BY category,skill_name").fetchall()
    mine=conn.execute("""SELECT s.skill_name FROM student_skills ss JOIN skills s ON s.id=ss.skill_id
                         WHERE ss.user_id=?""",(session["user_id"],)).fetchall()
    stream=get_student_stream(conn, session["user_id"])
    conn.close()
    mine_set={r["skill_name"] for r in mine}
    return render_template("skills.html", all_skills=all_skills, mine=mine_set, stream=stream, stream_domains=STREAM_DOMAINS)

@app.route("/job-roles")
def job_roles():
    if not login_required(): return redirect(url_for("login"))
    conn=db(); roles=conn.execute("SELECT * FROM job_roles ORDER BY role_name").fetchall(); conn.close()
    return render_template("job_roles.html", roles=roles)

@app.route("/analyze/<int:role_id>", methods=["GET","POST"])
def analyze(role_id):
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    role=conn.execute("SELECT * FROM job_roles WHERE id=?",(role_id,)).fetchone()
    if not role:
        conn.close(); return "Role not found",404
    student=[r["skill_name"] for r in conn.execute("""SELECT s.skill_name FROM student_skills ss
        JOIN skills s ON s.id=ss.skill_id WHERE ss.user_id=?""",(session["user_id"],)).fetchall()]
    required=[r["skill_name"] for r in conn.execute("""SELECT s.skill_name FROM job_role_skills jrs
        JOIN skills s ON s.id=jrs.skill_id WHERE jrs.job_role_id=? ORDER BY jrs.importance DESC""",(role_id,)).fetchall()]
    result=calculate_gap(student,required)
    if request.method=="POST":
        now=datetime.now().isoformat(timespec="seconds")
        cur=conn.execute("""INSERT INTO analyses(user_id,job_role_id,matched_count,missing_count,match_percent,created_at)
                            VALUES (?,?,?,?,?,?)""",(session["user_id"],role_id,len(result["matched"]),len(result["missing"]),result["match_percent"],now))
        aid=cur.lastrowid
        for s in result["matched"]:
            sid=conn.execute("SELECT id FROM skills WHERE skill_name=?",(s,)).fetchone()["id"]
            conn.execute("INSERT INTO analysis_skills(analysis_id,skill_id,status) VALUES (?,?,?)",(aid,sid,"matched"))
        for s in result["missing"]:
            sid=conn.execute("SELECT id FROM skills WHERE skill_name=?",(s,)).fetchone()["id"]
            conn.execute("INSERT INTO analysis_skills(analysis_id,skill_id,status) VALUES (?,?,?)",(aid,sid,"missing"))
        conn.commit(); conn.close()
        return redirect(url_for("analysis_result",analysis_id=aid))
    # Find this role's position in the student's Top 10 Best Matches.
    all_roles=conn.execute("""SELECT r.id, r.role_name, r.description, r.domain, GROUP_CONCAT(s.skill_name) reqs
                              FROM job_roles r JOIN job_role_skills j ON j.job_role_id=r.id
                              JOIN skills s ON s.id=j.skill_id GROUP BY r.id""").fetchall()
    ranking=sorted(role_recommendations(student, all_roles),
                   key=lambda x: (-x["match_percent"], -x["matched"], x["role_name"]))
    role_rank=next((i+1 for i,x in enumerate(ranking) if x.get("role_id")==role_id), None)
    medal = "🥇" if role_rank == 1 else ("🥈" if role_rank == 2 else ("🥉" if role_rank == 3 else ""))
    conn.close()
    return render_template("analyze.html",role=role,result=result,role_rank=role_rank,medal=medal)

@app.route("/analysis/<int:analysis_id>")
def analysis_result(analysis_id):
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    a=conn.execute("""SELECT a.*,r.role_name,r.description FROM analyses a JOIN job_roles r ON r.id=a.job_role_id
                      WHERE a.id=? AND a.user_id=?""",(analysis_id,session["user_id"])).fetchone()
    if not a: conn.close(); return "Analysis not found",404
    matched=conn.execute("""SELECT s.skill_name FROM analysis_skills x JOIN skills s ON s.id=x.skill_id
                            WHERE x.analysis_id=? AND x.status='matched'""",(analysis_id,)).fetchall()
    missing=conn.execute("""SELECT s.skill_name FROM analysis_skills x JOIN skills s ON s.id=x.skill_id
                            WHERE x.analysis_id=? AND x.status='missing'""",(analysis_id,)).fetchall()
    conn.close()
    roadmap=build_roadmap([r["skill_name"] for r in missing])
    return render_template("analysis.html",a=a,matched=matched,missing=missing,roadmap=roadmap)


@app.route("/report/<int:analysis_id>")
def download_report(analysis_id):
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    a=conn.execute("""SELECT a.*,r.role_name,r.description,r.domain FROM analyses a JOIN job_roles r ON r.id=a.job_role_id
                      WHERE a.id=? AND a.user_id=?""",(analysis_id,session["user_id"])).fetchone()
    if not a:
        conn.close(); return "Analysis not found",404
    user=conn.execute("SELECT name,email,education,college FROM users WHERE id=?",(session["user_id"],)).fetchone()
    matched=conn.execute("""SELECT s.skill_name FROM analysis_skills x JOIN skills s ON s.id=x.skill_id
                            WHERE x.analysis_id=? AND x.status='matched' ORDER BY s.skill_name""",(analysis_id,)).fetchall()
    missing=conn.execute("""SELECT s.skill_name FROM analysis_skills x JOIN skills s ON s.id=x.skill_id
                            WHERE x.analysis_id=? AND x.status='missing' ORDER BY s.skill_name""",(analysis_id,)).fetchall()
    conn.close()
    filename=os.path.join(BASE_DIR, f"skill_gap_report_{analysis_id}.pdf")
    styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(filename,pagesize=A4,rightMargin=40,leftMargin=40,topMargin=40,bottomMargin=40)
    story=[Paragraph("Student Skill Gap Analyzer",styles["Title"]),Spacer(1,12),
           Paragraph("Skill Gap Analysis Report",styles["Heading2"]),Spacer(1,10)]
    info=[["Student",user["name"]],["Email",user["email"]],["Domain",a["domain"]],["Target Role",a["role_name"]],
          ["Skill Match",f"{a['match_percent']:.0f}%"],["Matched Skills",str(a["matched_count"])],["Missing Skills",str(a["missing_count"])]]
    t=Table(info,colWidths=[130,380]); t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.grey),("BACKGROUND",(0,0),(0,-1),colors.whitesmoke),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),7)])); story += [t,Spacer(1,16)]
    story += [Paragraph("Skills You Have",styles["Heading3"]),Paragraph(", ".join(r["skill_name"] for r in matched) or "None",styles["BodyText"]),Spacer(1,12)]
    story += [Paragraph("Skills to Learn",styles["Heading3"]),Paragraph(", ".join(r["skill_name"] for r in missing) or "None",styles["BodyText"]),Spacer(1,12)]
    story += [Paragraph("Recommended Roadmap",styles["Heading3"])]
    for i, r in enumerate(build_roadmap([x["skill_name"] for x in missing]),1):
        story.append(Paragraph(f"{i}. {r}",styles["BodyText"]))
        story.append(Spacer(1,4))
    story.append(Spacer(1,14))
    story.append(Paragraph("Generated by Student Skill Gap Analyzer",styles["Italic"]))
    doc.build(story)
    return send_file(filename,as_attachment=True,download_name=f"Skill_Gap_Report_{analysis_id}.pdf")

@app.route("/roadmap/<int:analysis_id>")
def roadmap(analysis_id):
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    missing=conn.execute("""SELECT s.skill_name FROM analysis_skills x JOIN skills s ON s.id=x.skill_id
                            JOIN analyses a ON a.id=x.analysis_id
                            WHERE x.analysis_id=? AND x.status='missing' AND a.user_id=?""",(analysis_id,session["user_id"])).fetchall()
    conn.close()
    return render_template("roadmap.html", roadmap=build_roadmap([r["skill_name"] for r in missing]))

@app.route("/progress", methods=["GET","POST"])
def progress():
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    if request.method=="POST":
        try:
            sid=int(request.form["skill_id"]); pct=max(0,min(100,int(request.form["percentage"])))
        except (ValueError, KeyError):
            flash("Please enter a progress value from 0 to 100.","danger")
            return redirect(url_for("progress"))
        owns=conn.execute("SELECT 1 FROM student_skills WHERE user_id=? AND skill_id=?",(session["user_id"],sid)).fetchone()
        if not owns:
            conn.close(); return "Skill not found",404
        comp=1 if pct>=100 else 0
        conn.execute("""INSERT INTO progress(user_id,skill_id,percentage,completed,updated_at)
                        VALUES (?,?,?,?,?) ON CONFLICT(user_id,skill_id) DO UPDATE SET
                        percentage=excluded.percentage,completed=excluded.completed,updated_at=excluded.updated_at""",
                     (session["user_id"],sid,pct,comp,datetime.now().isoformat(timespec="seconds")))
        conn.commit(); flash("Skill progress updated.","success")
    rows=conn.execute("""SELECT s.id,s.skill_name,s.category,COALESCE(p.percentage,0) percentage,
                         COALESCE(p.completed,0) completed FROM student_skills ss
                         JOIN skills s ON s.id=ss.skill_id LEFT JOIN progress p
                         ON p.user_id=ss.user_id AND p.skill_id=s.id WHERE ss.user_id=? ORDER BY s.skill_name""",
                      (session["user_id"],)).fetchall()
    latest=conn.execute("""SELECT a.id,a.job_role_id,r.role_name FROM analyses a JOIN job_roles r ON r.id=a.job_role_id
                          WHERE a.user_id=? ORDER BY a.id DESC LIMIT 1""",(session["user_id"],)).fetchone()
    target_missing=[]
    target_role=None
    if latest:
        target_role=latest["role_name"]
        target_missing=conn.execute("""SELECT s.id,s.skill_name FROM analysis_skills x JOIN skills s ON s.id=x.skill_id
                                      WHERE x.analysis_id=? AND x.status='missing'""",(latest["id"],)).fetchall()
    conn.close()
    avg=round(sum(r["percentage"] for r in rows)/len(rows)) if rows else 0
    return render_template("progress.html", progress=rows, avg_progress=avg, target_role=target_role, target_missing=target_missing)

@app.route("/career-readiness")
def career_readiness():
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    student=[r["skill_name"] for r in conn.execute("SELECT s.skill_name FROM student_skills ss JOIN skills s ON s.id=ss.skill_id WHERE ss.user_id=?",(session["user_id"],)).fetchall()]
    prog=conn.execute("SELECT COALESCE(AVG(percentage),0) avg FROM progress WHERE user_id=?",(session["user_id"],)).fetchone()["avg"]
    latest=conn.execute("""SELECT a.*,r.role_name,r.domain FROM analyses a JOIN job_roles r ON r.id=a.job_role_id
                          WHERE a.user_id=? ORDER BY a.id DESC LIMIT 1""",(session["user_id"],)).fetchone()
    conn.close()
    match=float(latest["match_percent"]) if latest else 0
    # Readiness combines target-role match and demonstrated learning progress. It is an estimate, not a hiring prediction.
    readiness=round((match*0.65)+(float(prog)*0.35)) if latest else round(float(prog))
    return render_template("career_readiness.html", latest=latest, readiness=readiness, avg_progress=round(prog))

@app.route("/job-description", methods=["GET","POST"])
def job_description():
    if not login_required(): return redirect(url_for("login"))
    result=None; text=""
    if request.method=="POST":
        text=request.form.get("job_description","").strip()
        if len(text)<20:
            flash("Paste a longer job description so the analyzer can compare skills.","warning")
        else:
            conn=db()
            student=[r["skill_name"] for r in conn.execute("SELECT s.skill_name FROM student_skills ss JOIN skills s ON s.id=ss.skill_id WHERE ss.user_id=?",(session["user_id"],)).fetchall()]
            skill_rows=conn.execute("SELECT skill_name,category FROM skills ORDER BY skill_name").fetchall(); conn.close()
            lower=text.lower()
            detected=[r["skill_name"] for r in skill_rows if re.search(r"(?<![a-z0-9])"+re.escape(r["skill_name"].lower())+r"(?![a-z0-9])", lower)]
            matched=[x for x in detected if x.lower() in {s.lower() for s in student}]
            missing=[x for x in detected if x.lower() not in {s.lower() for s in student}]
            pct=round(len(matched)/len(detected)*100) if detected else 0
            result={"detected":detected,"matched":matched,"missing":missing,"match_percent":pct}
    return render_template("job_description.html", result=result, text=text)

@app.route("/skill-assessment", methods=["GET","POST"])
def skill_assessment():
    if not login_required(): return redirect(url_for("login"))
    quizzes={
      "Python":[("Which keyword defines a function?",["func","def","function","define"],1),("Which type is immutable?",["list","dict","set","tuple"],3),("What does len() return?",["Memory size","Number of items","Data type","Last index"],1),("Which library is common for data analysis?",["Pandas","Flask","Bootstrap","Jinja"],0),("What starts a comment?",["//","<!--","#","**"],2)],
      "SQL":[("Which command reads rows?",["SELECT","PUSH","READ","OPEN"],0),("Which clause filters rows?",["SORT","WHERE","GROUP","LIMIT"],1),("Which keyword combines tables?",["JOIN","MERGEONLY","LINK","ATTACH"],0),("Which function counts rows?",["TOTAL","COUNT","ROWS","NUMBER"],1),("Which command adds a row?",["ADD","INSERT","APPEND","PUT"],1)],
      "HTML":[("What does HTML define?",["Page structure","Database","Server logic","Network"],0),("Which tag creates a link?",["<link>","<a>","<url>","<href>"],1),("Which tag is a heading?",["<h1>","<head>","<title>","<p1>"],0),("Which attribute provides image text?",["src","alt","text","label"],1),("Which tag creates a form?",["<input>","<form>","<fieldset-only>","<data>"],1)],
      "CSS":[("CSS is mainly used for?",["Styling","Database","Authentication","Compilation"],0),("Which property changes text color?",["font","color","text","paint"],1),("Which layout system uses grid tracks?",["CSS Grid","HTML Grid","SQL Grid","DOM Grid"],0),("Which unit is relative to root font size?",["px","em","rem","pt"],2),("Which property rounds corners?",["corner-radius","border-radius","radius","round-border"],1)],
      "JavaScript":[("Which keyword declares a block-scoped variable?",["var","let","define","constvar"],1),("Which method adds an item to an array?",["push","add","appendOnly","insert"],0),("Which is a boolean value?",["yes","true","1true","on"],1),("Which API selects an element by id?",["getElementById","selectId","findId","queryIdOnly"],0),("JSON stands for?",["Java Syntax Object Notation","JavaScript Object Notation","Joined Script Object Names","None"],1)],
      "Accounting":[("Which statement records a company's financial position?",["Balance Sheet","Cash Memo","Purchase Order","Invoice"],0),("What is the basic accounting equation?",["Assets = Liabilities + Equity","Assets = Revenue - Expense","Profit = Assets + Cash","Equity = Sales + GST"],0),("Which account records money received from customers?",["Cash/Bank","Purchases","Depreciation","Capital only"],0),("What does GST stand for?",["Goods and Services Tax","General Sales Transfer","Government Service Tariff","Goods Supply Trade"],0),("Which document is commonly used to record a sale?",["Sales Invoice","Attendance Sheet","Balance Confirmation","Pay Slip"],0)],
      "Tally":[("Tally is mainly used for?",["Accounting and business records","Video editing","Web hosting","Graphic design"],0),("Which feature helps record GST transactions?",["GST features","Photo editor","Code compiler","Browser"],0),("Ledger is used to?",["Maintain account-wise transactions","Design logos","Store videos","Write CSS"],0),("A voucher is used to?",["Record a business transaction","Create a resume","Edit a photo","Build a webpage"],0),("Trial balance helps check?",["Arithmetical accuracy of ledger balances","Internet speed","Employee attendance","Website traffic"],0)],
      "Excel":[("Which symbol starts a formula in Excel?",["=","#","@","&"],0),("Which function adds values?",["SUM","JOIN","COUNTIFONLY","TOTALROW"],0),("Which feature summarizes data interactively?",["PivotTable","Slide Master","Mail Merge","Timeline"],0),("Which function finds the average?",["AVERAGE","MEANONLY","MID","AVGROW"],0),("Which tool is useful for filtering rows?",["Filter","Paint","Compiler","Debugger"],0)],
      "Financial Analysis":[("Which ratio measures short-term liquidity?",["Current Ratio","Debt Ratio","Gross Margin","ROE only"],0),("Revenue minus cost of goods sold gives?",["Gross Profit","Net Worth","Current Assets","Working Capital"],0),("Which statement reports cash inflows and outflows?",["Cash Flow Statement","Balance Sheet","Trial Balance","Purchase Register"],0),("ROI measures?",["Return relative to investment","Tax payable only","Inventory quantity","Employee count"],0),("Budget variance compares?",["Actual results with budgeted results","Assets with liabilities","Sales with customers","GST with TDS"],0)]
    }
    conn=db()
    stream=get_student_stream(conn, session["user_id"])
    current_skills=[r["skill_name"] for r in conn.execute("SELECT s.skill_name FROM student_skills ss JOIN skills s ON s.id=ss.skill_id WHERE ss.user_id=?",(session["user_id"],)).fetchall()]
    latest=conn.execute("""SELECT r.role_name FROM analyses a JOIN job_roles r ON r.id=a.job_role_id WHERE a.user_id=? ORDER BY a.id DESC LIMIT 1""",(session["user_id"],)).fetchone()
    conn.close()
    if stream in ("Commerce",):
        available=[x for x in ["Accounting","Tally","Excel","Financial Analysis"] if x in quizzes]
    elif stream == "Computer Science / IT":
        available=[x for x in ["Python","SQL","HTML","CSS","JavaScript"] if x in quizzes]
    else:
        available=[x for x in quizzes if x in current_skills]
    if not available: available=list(quizzes.keys())
    requested=request.values.get("skill","")
    skill=requested if requested in available else available[0]
    questions=quizzes[skill]; result=None
    if request.method=="POST":
        score=0
        for i,q in enumerate(questions):
            try: score += int(request.form.get(f"q{i}","-1"))==q[2]
            except ValueError: pass
        score=int(score); pct=score*20; result={"score":score,"total":len(questions),"percentage":pct}
        conn=db(); row=conn.execute("SELECT id FROM skills WHERE skill_name=?",(skill,)).fetchone()
        if row:
            old=conn.execute("SELECT percentage FROM progress WHERE user_id=? AND skill_id=?",(session["user_id"],row["id"])).fetchone()
            oldpct=old["percentage"] if old else 0
            newpct=max(oldpct,pct)
            conn.execute("""INSERT INTO progress(user_id,skill_id,percentage,completed,updated_at) VALUES (?,?,?,?,?)
              ON CONFLICT(user_id,skill_id) DO UPDATE SET percentage=excluded.percentage,completed=excluded.completed,updated_at=excluded.updated_at""",(session["user_id"],row["id"],newpct,1 if newpct>=100 else 0,datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        conn.close()
    return render_template("skill_assessment.html", skill=skill, questions=questions, result=result, skills=available, student_stream=stream, target_role=(latest["role_name"] if latest else ""))

@app.route("/recommendations")
def recommendations():
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    student=[r["skill_name"] for r in conn.execute("""SELECT s.skill_name FROM student_skills ss
        JOIN skills s ON s.id=ss.skill_id WHERE ss.user_id=?""",(session["user_id"],)).fetchall()]
    roles=conn.execute("""SELECT r.id, r.role_name, r.description, r.domain, GROUP_CONCAT(s.skill_name) reqs
                          FROM job_roles r JOIN job_role_skills j ON j.job_role_id=r.id
                          JOIN skills s ON s.id=j.skill_id GROUP BY r.id""").fetchall()
    stream=get_student_stream(conn, session["user_id"])
    conn.close()
    recs=role_recommendations(student,roles)
    if stream:
        allowed=set(STREAM_DOMAINS.get(stream, []))
        # Prefer the student's stream while still showing other roles only when there
        # are too few same-stream roles to make the list useful.
        role_domains={r["role_name"]: r["domain"] for r in roles}
        same=[r for r in recs if role_domains.get(r["role_name"]) in allowed]
        if len(same) >= 3:
            recs=same
    return render_template("recommendations.html", recs=recs, student_stream=stream)

@app.route("/profile", methods=["GET","POST"])
def profile():
    if not login_required(): return redirect(url_for("login"))
    conn=db()
    if request.method=="POST":
        conn.execute("UPDATE users SET name=?,education=?,college=?,education_stream=? WHERE id=?",
                     (request.form["name"],request.form.get("education",""),request.form.get("college",""),request.form.get("education_stream",""),session["user_id"]))
        conn.commit(); flash("Profile updated.","success")
    user=conn.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone(); conn.close()
    return render_template("profile.html", user=user)

@app.route("/admin")
def admin_dashboard():
    if not login_required(): return redirect(url_for("login"))
    conn=db(); user=conn.execute("SELECT role FROM users WHERE id=?",(session["user_id"],)).fetchone()
    if not user or user["role"] != "admin":
        conn.close(); return "Admin access required",403
    stats=conn.execute("SELECT COUNT(*) users FROM users").fetchone()
    analyses=conn.execute("SELECT COUNT(*) total FROM analyses").fetchone()
    high=conn.execute("SELECT COUNT(*) total FROM analyses WHERE match_percent < 40").fetchone()
    users=conn.execute("SELECT id,name,email,role,created_at FROM users ORDER BY id DESC LIMIT 20").fetchall()
    recent=conn.execute("""SELECT a.created_at,r.role_name,a.match_percent,u.name FROM analyses a JOIN job_roles r ON r.id=a.job_role_id JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 10""").fetchall()
    conn.close()
    return render_template("admin.html", stats=stats, analyses=analyses, high=high, users=users, recent=recent)

init_db()

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
