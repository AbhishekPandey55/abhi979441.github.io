import werkzeug
werkzeug.urls.url_decode = werkzeug.urls.url_unquote


from dotenv import load_dotenv
from decouple import config
import os
import atexit

# Load environment variables FIRST
load_dotenv()

# Flask and extension imports
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from config import Config
from datetime import datetime, timedelta
import secrets
from apscheduler.schedulers.background import BackgroundScheduler 
from apscheduler.triggers.cron import CronTrigger

# Initialize extensions
db = SQLAlchemy()
bcrypt = Bcrypt()
mail = Mail()
login_manager = LoginManager()
scheduler = BackgroundScheduler()

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
bcrypt.init_app(app)
mail.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'

# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    reset_token = db.Column(db.String(100))
    reminder_time = db.Column(db.String(5), default='08:00')
    plants = db.relationship('Plant', backref='owner', lazy=True)

# Plant model
class Plant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    plant_type = db.Column(db.String(100))
    last_watered = db.Column(db.String(20))
    water_frequency = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"Plant('{self.name}', '{self.plant_type}')"

# CREATE TABLES - ADDED THIS
with app.app_context():
    db.create_all()
    print("✅ Database tables created successfully!")

# User loader function
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes for user authentication
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        phone = request.form.get('phone', '')
        
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered. Please login.', 'danger')
            return redirect(url_for('login'))
        
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(email=email, password=hashed_password, phone=phone, reminder_time='08:00')
        
        db.session.add(user)
        db.session.commit()
        
        # Send welcome email
        send_welcome_email(user)
        
        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=remember)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Login failed. Please check your email and password.', 'danger')
    
    return render_template('login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Generate reset token
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            db.session.commit()
            
            # Send reset email
            send_password_reset_email(user, token)
            
        flash('If an account with that email exists, a password reset link has been sent.', 'info')
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    
    if not user:
        flash('Invalid or expired reset token.', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', token=token)
        
        user.password = bcrypt.generate_password_hash(password).decode('utf-8')
        user.reset_token = None
        db.session.commit()
        
        flash('Your password has been reset successfully. Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('reset_password.html', token=token)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# Settings page for personalized reminder time
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        reminder_time = request.form['reminder_time']
        
        # Validate time format (HH:MM)
        try:
            datetime.strptime(reminder_time, '%H:%M')
            current_user.reminder_time = reminder_time
            db.session.commit()
            
            # Reschedule reminders with new time
            schedule_watering_reminders()
            
            flash('Reminder time updated successfully!', 'success')
        except ValueError:
            flash('Please enter a valid time format (HH:MM).', 'danger')
        
        return redirect(url_for('settings'))
    
    return render_template('settings.html')

# Main application routes
@app.route('/')
@login_required
def index():
    plants = Plant.query.filter_by(user_id=current_user.id).order_by(Plant.name).all()
    
    # Check for plants that need watering today
    today = datetime.now().date()
    plants_needing_water = []
    plant_data = []
    
    for plant in plants:
        plant_info = {
            'plant': plant,
            'days_until_watering': None,
            'watering_status': 'unknown'
        }
        
        if plant.last_watered:
            try:
                last_watered_date = datetime.strptime(plant.last_watered, '%Y-%m-%d').date()
                next_watering_date = last_watered_date + timedelta(days=plant.water_frequency)
                days_until = (next_watering_date - today).days
                
                plant_info['days_until_watering'] = days_until
                
                # Determine watering status
                if days_until <= 0:
                    plant_info['watering_status'] = 'today'
                    plants_needing_water.append(plant)
                elif days_until == 1:
                    plant_info['watering_status'] = 'tomorrow'
                else:
                    plant_info['watering_status'] = 'future'
                    
            except ValueError:
                plant_info['watering_status'] = 'error'
                continue
        
        plant_data.append(plant_info)
    
    return render_template('index.html', 
                         plant_data=plant_data, 
                         plants_needing_water=plants_needing_water)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_plant():
    if request.method == 'POST':
        name = request.form['name']
        plant_type = request.form['plant_type']
        last_watered = request.form['last_watered']
        water_frequency = request.form['water_frequency']

        new_plant = Plant(
            name=name,
            plant_type=plant_type,
            last_watered=last_watered,
            water_frequency=water_frequency,
            user_id=current_user.id
        )

        db.session.add(new_plant)
        db.session.commit()
        
        flash(f'Plant "{name}" has been added successfully!', 'success')
        return redirect(url_for('index'))

    return render_template('add_plant.html')

@app.route('/delete/<int:plant_id>', methods=['POST'])
@login_required
def delete_plant(plant_id):
    # Ensure user can only delete their own plants
    plant = Plant.query.filter_by(id=plant_id, user_id=current_user.id).first_or_404()
    plant_name = plant.name
    
    db.session.delete(plant)
    db.session.commit()
    
    flash(f'Plant "{plant_name}" has been deleted.', 'danger')
    return redirect(url_for('index'))

@app.route('/water/<int:plant_id>', methods=['POST'])
@login_required
def water_plant(plant_id):
    plant = Plant.query.filter_by(id=plant_id, user_id=current_user.id).first_or_404()
    plant.last_watered = datetime.now().strftime('%Y-%m-%d')
    db.session.commit()
    
    flash(f'{plant.name} has been watered!', 'success')
    return redirect(url_for('index'))

@app.route('/plant-info')
@login_required
def plant_info():
    return render_template('plant_info.html')

# Email and notification functionality
def send_welcome_email(user):
    try:
        msg = Message(
            subject='🌿 Welcome to GreenThumb!',
            recipients=[user.email],
            html=f'''
            <!DOCTYPE html>
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #2e7d32, #4caf50); padding: 20px; text-align: center;">
                    <h1 style="color: white; margin: 0;">🌿 GreenThumb</h1>
                </div>
                <div style="padding: 20px; background: #f9f9f9;">
                    <h2>Welcome to GreenThumb, Plant Lover!</h2>
                    <p>We're thrilled to have you on board. With GreenThumb, you'll never forget to water your plants again.</p>
                    <p>Here's what you can do:</p>
                    <ul>
                        <li>Track all your plants in one place</li>
                        <li>Get reminders when it's time to water</li>
                        <li>Learn about plant care techniques</li>
                        <li>Watch your plant family grow!</li>
                    </ul>
                    <p>Start by adding your first plant to your collection!</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{url_for('index', _external=True)}" style="background: #4caf50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">Get Started</a>
                    </div>
                    <p>Happy planting! 🌱</p>
                    <p><strong>The GreenThumb Team</strong></p>
                </div>
                <div style="background: #e8f5e8; padding: 15px; text-align: center; font-size: 12px; color: #666;">
                    <p>© 2025 GreenThumb App. All rights reserved.</p>
                </div>
            </body>
            </html>
            '''
        )
        mail.send(msg)
    except Exception as e:
        print(f"Failed to send welcome email: {e}")

def send_password_reset_email(user, token):
    try:
        reset_url = url_for('reset_password', token=token, _external=True)
        msg = Message(
            subject='🔒 Reset Your GreenThumb Password',
            recipients=[user.email],
            html=f'''
            <!DOCTYPE html>
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #2e7d32, #4caf50); padding: 20px; text-align: center;">
                    <h1 style="color: white; margin: 0;">🌿 GreenThumb</h1>
                </div>
                <div style="padding: 20px; background: #f9f9f9;">
                    <h2>Password Reset Request</h2>
                    <p>We received a request to reset your password. Click the button below to create a new password:</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{reset_url}" style="background: #4caf50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">Reset Password</a>
                    </div>
                    <p>If you didn't request this reset, please ignore this email. Your password will remain unchanged.</p>
                    <p><strong>Note:</strong> This link will expire in 1 hour for security reasons.</p>
                </div>
                <div style="background: #e8f5e8; padding: 15px; text-align: center; font-size: 12px; color: #666;">
                    <p>© 2025 GreenThumb App. All rights reserved.</p>
                </div>
            </body>
            </html>
            '''
        )
        mail.send(msg)
    except Exception as e:
        print(f"Failed to send reset email: {e}")

def check_watering_reminders():
    with app.app_context():
        plants = Plant.query.all()
        today = datetime.now().date()
        
        for plant in plants:
            if plant.last_watered:
                try:
                    last_watered_date = datetime.strptime(plant.last_watered, '%Y-%m-%d').date()
                    next_watering_date = last_watered_date + timedelta(days=plant.water_frequency)
                    
                    # If plant needs watering today or is overdue
                    if today >= next_watering_date:
                        send_reminder_email(plant)
                except ValueError:
                    print(f"Invalid date format for plant {plant.id}")

def send_reminder_email(plant):
    try:
        user_email = plant.owner.email
        msg = Message(
            subject=f'💧 Time to water your {plant.name}!',
            recipients=[user_email],
            html=f'''
            <!DOCTYPE html>
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #2e7d32, #4caf50); padding: 20px; text-align: center;">
                    <h1 style="color: white; margin: 0;">💧 Watering Reminder</h1>
                </div>
                <div style="padding: 20px; background: #f9f9f9;">
                    <h2>Hello Plant Lover!</h2>
                    <p>It's time to give your <strong>{plant.name}</strong> some love! 💚</p>
                    
                    <div style="background: white; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #4caf50;">
                        <h3 style="margin-top: 0;">Plant Details:</h3>
                        <p><strong>Name:</strong> {plant.name}</p>
                        <p><strong>Type:</strong> {plant.plant_type or 'Not specified'}</p>
                        <p><strong>Last watered:</strong> {plant.last_watered}</p>
                        <p><strong>Water every:</strong> {plant.water_frequency} days</p>
                    </div>
                    
                    <p>Your plant will thank you for the hydration! 🌱</p>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{url_for('index', _external=True)}" style="background: #4caf50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">Mark as Watered</a>
                    </div>
                </div>
                <div style="background:极速赛车开奖结果记录
                    <p>© 2025 GreenThumb App. All rights reserved.</p>
                </div>
            </body>
            </html>
            '''
        )
        mail.send(msg)
        print(f"Reminder sent to {user_email} for {plant.name}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# Add this route to manually trigger reminders
@app.route('/send-reminders')
@login_required
def send_reminders():
    check_watering_reminders()
    flash('Watering reminders have been checked and sent!', 'info')
    return redirect(url_for('index'))

# Automated scheduling function
def schedule_watering_reminders():
    """Schedule reminder checks for each user's preferred time"""
    try:
        with app.app_context():
            # Check if database exists first
            try:
                # Try a simple query to check if database is accessible
                user_count = User.query.count()
                print(f"✅ Database accessible. Found {user_count} users.")
            except:
                print("⚠️  Database not ready yet. Skipping scheduler setup.")
                return
            
            # Clear existing jobs
            scheduler.remove_all_jobs()
            
            # Get all users and schedule for their preferred times
            users = User.query.all()
            for user in users:
                if user.reminder_time:
                    try:
                        # Parse the time (HH:MM)
                        hour, minute = map(int, user.reminder_time.split(':'))
                        
                        # Schedule job for this user's time
                        scheduler.add_job(
                            func=check_watering_reminders,
                            trigger=CronTrigger(hour=hour, minute=minute),
                            id=f'daily_watering_check_{user.id}',
                            name=f'Daily reminders for {user.email} at {user.reminder_time}',
                            replace_existing=True
                        )
                        print(f"✅ Scheduled reminders for {user.email} at {user.reminder_time}")
                        
                    except (ValueError, AttributeError):
                        print(f"❌ Invalid time format for user {user.email}: {user.reminder_time}")
                        continue
            
            if not scheduler.running:
                scheduler.start()
            print("✅ All reminder schedules set up successfully!")
            
    except Exception as e:
        print(f"❌ Failed to start scheduler: {e}")

# Scheduler shutdown handler
@atexit.register
def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        print("Scheduler shut down gracefully")

if __name__ == '__main__':
    # Start the app first, then try scheduler (but don't crash if it fails)
    print("🌿 Starting GreenThumb App...")
    
    # Try to start scheduler, but don't let it break the app
    try:
        schedule_watering_reminders()
        print("✅ Scheduler started successfully!")
    except Exception as e:
        print(f"⚠️  Scheduler not started (will work after database creation): {e}")
    
    print("🚀 App is running! Visit: http://localhost:5000")
    print("💡 Register a user first to create the database")

    app.run(debug=True)
