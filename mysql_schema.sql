CREATE DATABASE IF NOT EXISTS student_skill_analyzer;
USE student_skill_analyzer;

CREATE TABLE users (
 id INT AUTO_INCREMENT PRIMARY KEY,
 name VARCHAR(100) NOT NULL,
 email VARCHAR(150) UNIQUE NOT NULL,
 password VARCHAR(255) NOT NULL,
 education VARCHAR(150) DEFAULT '',
 college VARCHAR(200) DEFAULT '',
 created_at DATETIME NOT NULL
);

CREATE TABLE skills (
 id INT AUTO_INCREMENT PRIMARY KEY,
 skill_name VARCHAR(100) UNIQUE NOT NULL,
 category VARCHAR(50) NOT NULL
);

CREATE TABLE student_skills (
 id INT AUTO_INCREMENT PRIMARY KEY,
 user_id INT NOT NULL,
 skill_id INT NOT NULL,
 source VARCHAR(30) DEFAULT 'Manual',
 confidence DECIMAL(4,2) DEFAULT 1.00,
 UNIQUE(user_id,skill_id),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

CREATE TABLE job_roles (
 id INT AUTO_INCREMENT PRIMARY KEY,
 role_name VARCHAR(100) UNIQUE NOT NULL,
 description TEXT NOT NULL
);

CREATE TABLE job_role_skills (
 id INT AUTO_INCREMENT PRIMARY KEY,
 job_role_id INT NOT NULL,
 skill_id INT NOT NULL,
 importance INT DEFAULT 3,
 UNIQUE(job_role_id,skill_id),
 FOREIGN KEY(job_role_id) REFERENCES job_roles(id) ON DELETE CASCADE,
 FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

CREATE TABLE analyses (
 id INT AUTO_INCREMENT PRIMARY KEY,
 user_id INT NOT NULL,
 job_role_id INT NOT NULL,
 matched_count INT NOT NULL,
 missing_count INT NOT NULL,
 match_percent DECIMAL(6,2) NOT NULL,
 created_at DATETIME NOT NULL,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(job_role_id) REFERENCES job_roles(id) ON DELETE CASCADE
);

CREATE TABLE analysis_skills (
 id INT AUTO_INCREMENT PRIMARY KEY,
 analysis_id INT NOT NULL,
 skill_id INT NOT NULL,
 status VARCHAR(20) NOT NULL,
 FOREIGN KEY(analysis_id) REFERENCES analyses(id) ON DELETE CASCADE,
 FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

CREATE TABLE learning_resources (
 id INT AUTO_INCREMENT PRIMARY KEY,
 skill_id INT NOT NULL,
 title VARCHAR(200) NOT NULL,
 description TEXT NOT NULL,
 url TEXT,
 difficulty VARCHAR(30) DEFAULT 'Beginner',
 FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

CREATE TABLE progress (
 id INT AUTO_INCREMENT PRIMARY KEY,
 user_id INT NOT NULL,
 skill_id INT NOT NULL,
 percentage INT DEFAULT 0,
 completed BOOLEAN DEFAULT FALSE,
 updated_at DATETIME NOT NULL,
 UNIQUE(user_id,skill_id),
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
