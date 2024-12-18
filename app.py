from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore, auth

# Initialize Flask App
app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Initialize Firebase
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Ensure uploads directory exists
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def is_authenticated():
    return 'user' in session and 'role' in session


@app.route('/google-login', methods=['POST'])
def google_login():
    try:
        data = request.get_json()
        token = data.get('token')
        print(f"Received token: {token}")

        # Verify the Firebase ID token
        decoded_token = auth.verify_id_token(token)
        user_email = decoded_token['email']
        print(f"Decoded token email: {user_email}")

        # Check if the user exists in your database
        user_doc = db.collection('users').document(user_email).get()
        if not user_doc.exists:
            print('User not found in the database.')
            flash('User not found in the database.')
            return jsonify({'success': False})

        # Get the user's role from the database
        role = user_doc.to_dict().get('role')
        print(f"User {user_email} has role {role}")

        # Store user session and role in the session
        session['user'] = user_email
        session['role'] = role  # Get the role from Firebase
        
        # Send the role back in the response
        return jsonify({'success': True, 'role': role})

    except Exception as e:
        print(f"Error during Google login: {e}")
        return jsonify({'success': False})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        print(f"Attempting to login with email: {email}")

        try:
            user = auth.get_user_by_email(email)
            print(f"Fetched user: {user.email}")
            
            user_doc = db.collection('users').document(email).get()
            if user_doc.exists:
                session['user'] = email
                session['role'] = user_doc.to_dict().get('role')  # Storing role in session
                if session['role'] == 'teacher':
                    flash(f'Welcome, {email}!')
                    return redirect(url_for('teachers_home'))
                else:
                    flash('Access denied! Only teachers can log in.')
                    print('Access denied: User is not a teacher.')
            else:
                flash('User not found.')
        except Exception as e:
            flash(f'Login failed: {e}')
            print(f"Login error: {e}")
    return render_template('login.html')

@app.route('/teachers_home')
def teachers_home():
    if not is_authenticated():
        return redirect(url_for('login'))

    if session.get('role') != 'teacher':
        flash('You do not have access to this page.')
        return redirect(url_for('login'))

    classrooms_ref = db.collection('classrooms').stream()
    classrooms = {doc.id: doc.to_dict() for doc in classrooms_ref}
    return render_template('teachers_home.html', classrooms=classrooms)
    
@app.route('/students_home')
def students_home():
    if not is_authenticated():
        return redirect(url_for('login'))

    if session.get('role') != 'student':
        flash('You do not have access to this page.')
        return redirect(url_for('login'))

    # Fetch classrooms and assigned projects for the student
    student_email = session['user']
    classrooms_ref = db.collection('classrooms').stream()
    
    classrooms = {}
    for classroom_doc in classrooms_ref:
        class_name = classroom_doc.id
        projects_ref = classroom_doc.reference.collection('Projects').stream()
        projects = []
        
        for project_doc in projects_ref:
            project = project_doc.to_dict()
            projects.append(project)
        
        classrooms[class_name] = projects

    return render_template('students_home.html', classrooms=classrooms)



@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('You have been logged out.')
    return redirect(url_for('login'))

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if not is_authenticated():
        return redirect(url_for('login'))
    if request.method == 'POST':
        class_name = request.form['class_name']
        csv_file = request.files['student_csv']

        if not class_name or not csv_file:
            flash('Both class name and CSV file are required.')
            return redirect(url_for('upload'))

        csv_path = os.path.join(UPLOAD_FOLDER, csv_file.filename)
        csv_file.save(csv_path)

        try:
            df = pd.read_csv(csv_path)
            if 'name' not in df.columns or 'email' not in df.columns:
                flash('CSV must have "name" and "email" columns!')
                return redirect(url_for('upload'))

            classroom_ref = db.collection('classrooms').document(class_name)
            classroom_ref.set({'classID': class_name})
            for _, row in df.iterrows():
                classroom_ref.collection('students').document(row['email']).set(
                    {
                        'firstName': row['name'].split()[0],
                        'lastName': row['name'].split()[-1],
                        'email': row['email'],
                        'assignedAt': firestore.SERVER_TIMESTAMP
                    }
                )
            flash(f'Classroom "{class_name}" created successfully!')
            return redirect(url_for('teachers_home'))

        except Exception as e:
            flash(f'Error processing CSV: {e}')
            return redirect(url_for('upload'))
        finally:
            os.remove(csv_path)
    return render_template('upload.html')

@app.route('/classroom/<class_name>')
def classroom_view(class_name):
    if not is_authenticated():
        return redirect(url_for('login'))
    classroom_ref = db.collection('classrooms').document(class_name)
    classroom = classroom_ref.get().to_dict()
    projects_ref = classroom_ref.collection('Projects').stream()
    projects = {proj.id: proj.to_dict() for proj in projects_ref}
    return render_template('classroom.html', class_name=class_name, projects=projects)

@app.route('/classroom/<class_name>/add_project', methods=['GET', 'POST'])
def add_project(class_name):
    if not is_authenticated():
        return redirect(url_for('login'))
    if request.method == 'POST':
        project_name = request.form['project_name']
        if not project_name:
            flash("Project name is required.")
            return redirect(url_for('add_project', class_name=class_name))

        project_ref = db.collection('classrooms').document(class_name).collection('Projects').document(project_name)
        project_ref.set({
            'projectName': project_name,
            'dueDate': request.form.get('due_date', ''),
            'Description': request.form.get('description', '')
        })
        flash(f"Project {project_name} added to {class_name}.")
        return redirect(url_for('classroom_view', class_name=class_name))
    return render_template('add_project.html', class_name=class_name)

@app.route('/classroom/<class_name>/project/<project_name>')
def project_view(class_name, project_name):
    if not is_authenticated():
        return redirect(url_for('login'))
    project_ref = db.collection('classrooms').document(class_name).collection('Projects').document(project_name)
    project = project_ref.get().to_dict()
    teams_ref = project_ref.collection('teams').stream()
    teams = {team.id: team.to_dict() for team in teams_ref}
    return render_template('project.html', class_name=class_name, project_name=project_name, teams=teams)

@app.route('/classroom/<class_name>/project/<project_name>/add_team', methods=['GET', 'POST'])
def add_team(class_name, project_name):
    if not is_authenticated():
        return redirect(url_for('login'))
    if request.method == 'POST':
        team_name = request.form['team_name']
        selected_students = request.form.getlist('students')

        if not team_name or not selected_students:
            flash('Team name and at least one student are required.')
            return redirect(url_for('add_team', class_name=class_name, project_name=project_name))

        team_ref = db.collection('classrooms').document(class_name).collection('Projects').document(project_name).collection('teams').document(team_name)
        team_ref.set({
            'members': selected_students
        })
        flash(f'Team "{team_name}" created successfully!')
        return redirect(url_for('project_view', class_name=class_name, project_name=project_name))

    all_students = db.collection('classrooms').document(class_name).collection('students').stream()
    assigned_students = set()
    teams_ref = db.collection('classrooms').document(class_name).collection('Projects').document(project_name).collection('teams').stream()
    for team in teams_ref:
        assigned_students.update(team.to_dict().get('members', []))

    available_students = [s.to_dict() for s in all_students if s.id not in assigned_students]

    return render_template(
        'add_team.html',
        class_name=class_name, project_name=project_name, students=available_students
    )

@app.route('/classroom/<class_name>/project/<project_name>/team/<team_name>')
def team_view(class_name, project_name, team_name):
    if not is_authenticated():
        return redirect(url_for('login'))
    team_ref = db.collection('classrooms').document(class_name).collection('Projects').document(project_name).collection('teams').document(team_name)
    team = team_ref.get().to_dict()
    return render_template(
        'team.html',
        class_name=class_name, project_name=project_name, team_name=team_name, team_members=team.get('members', [])
    )

if __name__ == '__main__':
    app.run(debug=True)
