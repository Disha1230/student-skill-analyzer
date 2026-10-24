# Student Skill Gap Analyzer

A final-year project built with Python + Flask + SQLite. It analyzes a student's resume, detects technical skills, compares them with a target job role, calculates skill match/gap percentages, generates a learning roadmap, and tracks progress.

## Features
- Registration and login
- Student profile
- PDF resume upload
- NLP-style skill extraction using a curated technical-skill dictionary
- Skill matching and gap percentage
- Six predefined job roles
- Learning roadmap
- Progress tracking
- Job-role skill-match recommendations
- SQLite database created automatically on first run
- MySQL schema included in `database/mysql_schema.sql`

## Run on Windows
1. Install Python 3.11+.
2. Open Command Prompt in this folder.
3. Run:
   `py -m pip install -r requirements.txt`
4. Run:
   `py app.py`
5. Open:
   `http://127.0.0.1:5000`

The SQLite database `student_skill_analyzer.db` is created automatically.

## Demo
Register -> Login -> Upload a text-based PDF resume -> Skills -> Job Roles -> Analyze -> Save Analysis -> View Roadmap -> Track Progress.

## AI explanation for viva
The project uses PDF text extraction, rule/dictionary-based NLP skill extraction, set-based skill matching, percentage calculation, and a recommendation/roadmap engine. This is intentionally API-free, so it works without an OpenAI or other paid API key.

## Important
For production, change the Flask secret key and use a production server. Resume files should also be validated and stored securely.

## Stream-aware career personalization
The PRO version adapts the student experience to the selected education stream. Commerce students see Commerce skills and assessments (Accounting, Tally, Excel, Financial Analysis), while CS/IT students see technical skills and assessments. Existing users without a stream are automatically inferred from their saved skills where possible.

The selected stream also focuses dashboard/recommendation results on relevant career domains, while the Job Roles explorer still lets students browse other domains when they want to explore.
