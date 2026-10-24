"""Create or promote an admin account. Run: python create_admin.py"""
from getpass import getpass
from werkzeug.security import generate_password_hash
from app import init_db, db
init_db()
email=input('Admin email: ').strip().lower(); name=input('Admin name: ').strip() or 'Administrator'; password=getpass('Admin password: ')
conn=db(); row=conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
if row:
    conn.execute('UPDATE users SET role="admin", name=?, password=? WHERE id=?',(name,generate_password_hash(password),row['id']))
else:
    from datetime import datetime
    conn.execute('INSERT INTO users(name,email,password,role,created_at) VALUES (?,?,?,?,?)',(name,email,generate_password_hash(password),'admin',datetime.now().isoformat(timespec='seconds')))
conn.commit(); conn.close(); print('Admin account ready.')
