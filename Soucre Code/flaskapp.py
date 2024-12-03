from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response
from flask_sqlalchemy import SQLAlchemy
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
import pandas as pd
import plotly.express as px
import os

# Azure Blob Storage configuration
AZURE_CONNECTION_STRING = ""
AZURE_CONTAINER_NAME = "datasets"

blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')  # Upload folder
db = SQLAlchemy(app)

# Allowed file extensions for uploads
ALLOWED_EXTENSIONS = {'csv'}

# Ensure the upload folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Global variable for dataset
data = pd.DataFrame()

def allowed_file(filename):
    """Check if the uploaded file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# User Model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['login_input']  # Either username or email
        password = request.form['password']
        user = User.query.filter(
            (User.username == login_input) | (User.email == login_input),
            User.password == password
        ).first()
        if user:
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Error: Username already exists. Please choose a different username.', 'danger')
            return render_template('register.html')
        elif User.query.filter_by(email=email).first():
            flash('Error: Email already registered. Please use a different email.', 'danger')
            return render_template('register.html')
        else:
            new_user = User(username=username, email=email, password=password)
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful!', 'success')
            return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    global data

    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = db.session.get(User, session['user_id'])

    start_row = 0  # Start from row 0 by default
    end_row = 25   # Show 25 rows by default

    if request.method == 'POST':
        try:
            start_row = int(request.form.get('start_row', start_row))
            end_row = int(request.form.get('end_row', end_row))
            if end_row > len(data):
                end_row = len(data)
        except ValueError:
            flash("Invalid row range input. Please enter valid numbers.", "danger")
            return redirect(url_for('dashboard'))

    filtered_data = data.iloc[start_row:end_row] if not data.empty else pd.DataFrame()
    data_html = None
    pie_chart = None
    bar_chart = None
    if not filtered_data.empty and 'readmitted' in filtered_data.columns:
        # Define consistent color mapping
        color_map = {'NO': 'blue', '>30': 'red', '<30': 'green', 'YES': 'orange'}
        category_order = {'readmitted': ['NO', '>30', '<30']}

        # Convert the data to HTML and pass it to the template
        data_html = filtered_data.to_html(classes='data', index=False).replace('\n', '')

        # Create Pie Chart
        fig_pie = px.pie(filtered_data, names='readmitted', color='readmitted', color_discrete_map=color_map, category_orders=category_order, title="Readmission Status Distribution", labels={'readmitted': 'Readmission Status'}, hole=0.05)
        fig_pie.update_traces(textinfo="percent+label", textfont_size=12)
        pie_chart = fig_pie.to_html(full_html=False, include_plotlyjs='cdn').replace('\n', '')  # Embed Plotly.js from CDN

        # Create Bar Chart
        grouped_data = filtered_data.groupby(['age', 'readmitted']).size().reset_index(name='count')
        fig_bar = px.bar(grouped_data, x='age', y='count', color='readmitted', color_discrete_map=color_map, category_orders=category_order, title="Correlation of Age to Readmission Status", barmode='group', labels={'readmitted': 'Readmission Status', "count": "Patient Count", 'age': 'Age Group'})
        bar_chart = fig_bar.to_html(full_html=False).replace('\n', '')

    return render_template('dashboard.html', tables=data_html, titles=data.columns.values, pie_chart=pie_chart, bar_chart=bar_chart, start_row=start_row, end_row=end_row, user=user)

@app.route('/upload_dataset', methods=['POST'])
def upload_dataset():
    global data

    if 'file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('dashboard'))

    if file and allowed_file(file.filename):
        # Upload the file to Azure Blob Storage
        try:
            # Create a BlobClient to upload the file
            blob_client = container_client.get_blob_client(file.filename)

            # Upload the file to Azure Blob Storage
            blob_client.upload_blob(file, overwrite=True)
            flash('Dataset uploaded to Azure Blob Storage successfully!', 'success')

            # Read the file from Azure Blob Storage into the DataFrame
            download_stream = blob_client.download_blob()
            data = pd.read_csv(download_stream)

            flash('Dataset loaded into the application!', 'success')
        except Exception as e:
            flash(f'Error uploading/loading dataset: {e}', 'danger')
    else:
        flash('Invalid file type. Please upload a CSV file.', 'danger')

    return redirect(url_for('dashboard'))

@app.route('/download_dataset', methods=['POST'])
def download_dataset():
    global data

    if data.empty:
        flash('No dataset available to download.', 'danger')
        return redirect(url_for('dashboard'))

    # Convert DataFrame to CSV and upload to Azure Blob Storage
    csv_data = data.to_csv(index=False)
    filename = "filtered_dataset.csv"

    # Create a BlobClient and upload the CSV data
    blob_client = container_client.get_blob_client(filename)
    blob_client.upload_blob(csv_data, overwrite=True)

    # Serve the file to the user
    response = make_response(csv_data)
    response.headers['Content-Disposition'] = 'attachment; filename=filtered_dataset.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Logged out successfully!', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():  # Ensure the application context is active
        db.create_all()      # Create the database tables
    app.run(debug=True)
