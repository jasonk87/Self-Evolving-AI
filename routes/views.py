
from flask import Blueprint, render_template, current_app
from . import views_bp

@views_bp.route('/')
def index():
    return render_template('index.html')

@views_bp.route('/favicon.ico')
def favicon():
    return current_app.send_static_file('favicon.ico')
