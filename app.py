from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash
from pymongo import MongoClient
from bson.objectid import ObjectId
import os
import random
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Any
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from io import BytesIO
import json
from bson.errors import InvalidId

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Replace with your actual MongoDB URI
MONGO_URI = "mongodb+srv://fortunedwards:oselumese@universityschedulerclus.rqbvhmw.mongodb.net/?retryWrites=true&w=majority&appName=UniversitySchedulerCluster"
client = MongoClient(MONGO_URI)
db = client['university_scheduler']

teachers_collection = db['teachers']
rooms_collection = db['rooms']
courses_collection = db['courses']
saved_timetables_collection = db['saved_timetables']
departments_collection = db['departments']


# --- Settings Management ---
SETTINGS_FILE = 'settings.json'
DEFAULT_SETTINGS = {
    'ga_population_size': 100,
    'ga_generations': 200,
    'ga_mutation_rate': 0.05,
    'ga_crossover_rate': 0.7,
    'max_lecturer_hours_per_week': 20
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def get_settings():
    return load_settings()

def get_setting(key):
    return get_settings().get(key)

def update_setting(key, value):
    settings = load_settings()
    settings[key] = value
    save_settings(settings)

# Ensure settings file exists on startup
if not os.path.exists(SETTINGS_FILE):
    save_settings(DEFAULT_SETTINGS)

# --- Helper Functions ---
def convert_objectids_to_strings(data):
    if isinstance(data, ObjectId):
        return str(data)
    elif isinstance(data, list):
        return [convert_objectids_to_strings(item) for item in data]
    elif isinstance(data, dict):
        new_dict = {}
        for key, value in data.items():
            if isinstance(value, ObjectId):
                new_dict[key] = str(value)
            elif isinstance(value, (list, dict)):
                new_dict[key] = convert_objectids_to_strings(value)
            else:
                new_dict[key] = value
        return new_dict
    else:
        return data

# --- Time and Day Configuration ---
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
START_TIME_STR = "08:00"
END_TIME_STR = "18:00"
CLASS_DURATION_MINS = 120

# Generate time slots in 2-hour increments
start_time_obj = datetime.strptime(START_TIME_STR, "%H:%M")
end_time_obj = datetime.strptime(END_TIME_STR, "%H:%M")
time_slots = []
display_time_slots = []
current_time = start_time_obj
while current_time < end_time_obj:
    time_slots.append(current_time)
    end_of_slot = current_time + timedelta(minutes=CLASS_DURATION_MINS)
    display_time_slots.append(f"{current_time.strftime('%I:%M %p')} - {end_of_slot.strftime('%I:%M %p')}")
    current_time = end_of_slot

# --- Constraint Satisfaction (Backtracking) Algorithm ---
def backtracking_algorithm(courses, rooms, teachers):
    all_courses = {str(c['_id']): c for c in courses}
    all_rooms = {str(r['_id']): r for r in rooms}
    all_teachers = {str(t['_id']): t for t in teachers}
    
    events_to_schedule = []
    for course in courses:
        # Each course needs to be scheduled for its lectures_per_week times
        for _ in range(course.get('number_of_lectures_per_week', 0)):
            events_to_schedule.append(course['_id'])
    
    if not events_to_schedule:
        return []

    occupied_slots_room = defaultdict(list)
    occupied_slots_lecturer = defaultdict(list)
    occupied_slots_level_dept = defaultdict(list) # Track level and department conflicts together

    final_schedule = []
    
    def is_valid_assignment(course_id, day, time_slot, room_id):
        course_data = all_courses.get(str(course_id))
        if not course_data: return False

        course_level = course_data.get('level')
        course_department_ids = [str(did) for did in course_data.get('department_ids', [])]
        room_capacity = all_rooms.get(str(room_id), {}).get('capacity', 0)
        course_students = course_data.get('number_of_students', 0)
        
        if room_id in occupied_slots_room[(day, time_slot)]:
            return False
        
        course_lecturer_ids = [ObjectId(lid) for lid in course_data.get('lecturer_ids', [])]
        if any(lid in occupied_slots_lecturer[(day, time_slot)] for lid in course_lecturer_ids):
            return False
            
        # Updated conflict check: Conflict only if both level AND department match
        # Iterate through the course's departments to check for conflicts
        for dept_id in course_department_ids:
            if (course_level, dept_id) in occupied_slots_level_dept[(day, time_slot)]:
                return False

        if room_capacity < course_students:
            return False
            
        return True

    def solve_csp(index):
        if index == len(events_to_schedule):
            return True

        course_id = events_to_schedule[index]
        course_data = all_courses.get(str(course_id))
        
        if not course_data:
            return solve_csp(index + 1)
        
        # Introduce randomization here
        shuffled_days = list(DAYS)
        random.shuffle(shuffled_days)
        shuffled_time_slots = list(time_slots)
        random.shuffle(shuffled_time_slots)
        shuffled_rooms = list(rooms)
        random.shuffle(shuffled_rooms)

        for day in shuffled_days:
            for time_slot in shuffled_time_slots:
                for room in shuffled_rooms:
                    if is_valid_assignment(course_id, day, time_slot, room['_id']):
                        occupied_slots_room[(day, time_slot)].append(room['_id'])
                        
                        course_lecturer_ids = [ObjectId(lid) for lid in course_data.get('lecturer_ids', [])]
                        for lid in course_lecturer_ids:
                            occupied_slots_lecturer[(day, time_slot)].append(lid)
                        
                        # Update the occupied slots to track by level and department
                        course_department_ids = [str(did) for did in course_data.get('department_ids', [])]
                        for dept_id in course_department_ids:
                            occupied_slots_level_dept[(day, time_slot)].append((course_data['level'], dept_id))
                        
                        final_schedule.append({
                            'course_id': course_id,
                            'room_id': room['_id'],
                            'day': day,
                            'time_slot': time_slot,
                        })

                        if solve_csp(index + 1):
                            return True

                        final_schedule.pop()
                        occupied_slots_room[(day, time_slot)].remove(room['_id'])
                        for lid in course_lecturer_ids:
                            occupied_slots_lecturer[(day, time_slot)].remove(lid)
                        
                        for dept_id in course_department_ids:
                            occupied_slots_level_dept[(day, time_slot)].remove((course_data['level'], dept_id))
        return False

    if solve_csp(0):
        return final_schedule
    else:
        return None

def get_display_timetable(individual):
    if not individual:
        return {}
        
    timetable_dict = defaultdict(lambda: defaultdict(list))
    
    all_courses = {str(c['_id']): c for c in courses_collection.find()}
    all_rooms = {str(r['_id']): r for r in rooms_collection.find()}
    all_teachers = {str(t['_id']): t for t in teachers_collection.find()}

    for event in individual:
        day = event['day']
        
        if isinstance(event['time_slot'], datetime):
            time_slot_obj = event['time_slot']
            end_of_slot = time_slot_obj + timedelta(minutes=CLASS_DURATION_MINS)
            time_slot_str = f"{time_slot_obj.strftime('%I:%M %p')} - {end_of_slot.strftime('%I:%M %p')}"
        else:
            time_slot_str = event['time_slot']
        
        course_data = all_courses.get(str(event.get('course_id')))
        room_data = all_rooms.get(str(event.get('room_id')))
        
        if course_data and room_data:
            lecturer_ids = course_data.get('lecturer_ids', [])
            lecturer_names = [all_teachers.get(str(lid), {}).get('name') for lid in lecturer_ids]
            
            timetable_dict[day][time_slot_str].append({
                'course_name': course_data.get('name', 'Unknown Course'),
                'lecturer_names': lecturer_names,
                'room_name': room_data.get('name', 'Unknown Room'),
                'department_ids': course_data.get('department_ids', [])
            })
                
    return timetable_dict


def generate_pdf_from_timetable(timetable_data, header_text, department_name=None):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), title=header_text)
    styles = getSampleStyleSheet()

    elements = []
    
    header_style = styles['h1']
    header_style.alignment = 1
    elements.append(Paragraph(header_text, header_style))
    
    # Filter timetable data if a department name is provided
    filtered_timetable_data = defaultdict(lambda: defaultdict(list))
    if department_name:
        all_departments = {str(d['_id']): d['name'] for d in departments_collection.find()}
        dept_id_to_filter = next((d_id for d_id, d_name in all_departments.items() if d_name == department_name), None)

        if dept_id_to_filter:
            for day, slots in timetable_data.items():
                for time_slot_str, events in slots.items():
                    for event in events:
                        if str(ObjectId(dept_id_to_filter)) in [str(d_id) for d_id in event.get('department_ids', [])]:
                            filtered_timetable_data[day][time_slot_str].append(event)
        timetable_data = filtered_timetable_data

    table_data = [[''] + display_time_slots]
    
    for day in DAYS:
        row = [day]
        for time_slot_str in display_time_slots:
            events = timetable_data.get(day, {}).get(time_slot_str, [])
            cell_content_parts = []
            for event in events:
                cell_content_parts.append(f"<b>{event['course_name']}</b><br/>{event['room_name']}")
            cell_content = '<br/><br/>'.join(cell_content_parts)
            
            p = Paragraph(cell_content, styles['Normal'])
            row.append(p)
        table_data.append(row)

    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F5F5F5')),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ])

    page_width = landscape(letter)[0]
    total_col_width = 72 * 10.5
    time_slot_col_width = (total_col_width - 1.5*72) / len(display_time_slots)
    col_widths = [1.5*72] + [time_slot_col_width] * len(display_time_slots)
    
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(table_style)
    elements.append(table)

    elements.append(Spacer(1, 20))
    elements.append(Paragraph("<b>Course Lecturers</b>", styles['h2']))
    elements.append(Spacer(1, 10))
    
    course_lecturers = {}
    for day_data in timetable_data.values():
        for time_slot_data in day_data.values():
            for event in time_slot_data:
                course_name = event['course_name']
                lecturer_names = event['lecturer_names']
                course_lecturers[course_name] = lecturer_names

    for course, lecturers in sorted(course_lecturers.items()):
        lecturers_str = ', '.join(lecturers) if lecturers else 'N/A'
        p = Paragraph(f"<b>{course}</b>: {lecturers_str}", styles['Normal'])
        elements.append(p)
        elements.append(Spacer(1, 5))
        
    doc.build(elements)
    
    buffer.seek(0)
    return buffer


# --- Routes ---
@app.route('/')
def index():
    saved_timetables = list(saved_timetables_collection.find())
    saved_timetables = convert_objectids_to_strings(saved_timetables)
    return render_template('index.html', saved_timetables=saved_timetables, active_page='index')

@app.route('/delete_saved_timetable/<timetable_id>', methods=['POST'])
def delete_saved_timetable(timetable_id):
    result = saved_timetables_collection.delete_one({'_id': ObjectId(timetable_id)})
    if result.deleted_count == 1:
        return jsonify({'success': True, 'message': 'Timetable deleted successfully.'})
    return jsonify({'success': False, 'message': 'Timetable not found.'})

@app.route('/data_management/<type>')
def data_management(type):
    data = []
    if type == 'lecturers':
        data = list(teachers_collection.find().sort("name", 1))
    elif type == 'rooms':
        data = list(rooms_collection.find().sort("name", 1))
    elif type == 'departments':
        data = list(departments_collection.find().sort("name", 1))
    elif type == 'courses':
        data = list(courses_collection.find().sort("name", 1))
    
    data = convert_objectids_to_strings(data)
    
    all_departments = {str(d['_id']): d['name'] for d in departments_collection.find()}
    all_lecturers = {str(l['_id']): l['name'] for l in teachers_collection.find()}
    all_departments_list = convert_objectids_to_strings(list(departments_collection.find()))
    all_lecturers_list = convert_objectids_to_strings(list(teachers_collection.find()))
    
    return render_template(
        'data_management.html',
        data=data,
        active_tab=type,
        all_lecturers=all_lecturers,
        all_lecturers_list=all_lecturers_list,
        all_departments=all_departments,
        all_departments_list=all_departments_list,
        active_page='data_management'
    )

@app.route('/add_data/<type>', methods=['POST'])
def add_data(type):
    new_data = request.json
    
    if type == 'lecturers':
        teachers_collection.insert_one(new_data)
    elif type == 'rooms':
        new_data['capacity'] = int(new_data.get('capacity', 0))
        rooms_collection.insert_one(new_data)
    elif type == 'departments':
        departments_collection.insert_one(new_data)
    elif type == 'courses':
        new_data['department_ids'] = [ObjectId(did) for did in new_data.get('department_ids', [])]
        new_data['lecturer_ids'] = [ObjectId(lid) for lid in new_data.get('lecturer_ids', [])]
        new_data['number_of_students'] = int(new_data.get('number_of_students', 0))
        new_data['level'] = new_data.get('level', 'Unknown')
        courses_collection.insert_one(new_data)
        
    return jsonify({'success': True, 'message': f'{type.capitalize()} added successfully.'})

@app.route('/get_data/<type>/<item_id>')
def get_data(type, item_id):
    collection = None
    if type == 'lecturers':
        collection = teachers_collection
    elif type == 'rooms':
        collection = rooms_collection
    elif type == 'departments':
        collection = departments_collection
    elif type == 'courses':
        collection = courses_collection
    else:
        return jsonify({'success': False, 'message': 'Invalid data type.'}), 400
    
    try:
        data = collection.find_one({'_id': ObjectId(item_id)})
    except InvalidId:
        return jsonify({'success': False, 'message': 'Invalid ID format.'}), 400

    if data:
        data = convert_objectids_to_strings(data)
        return jsonify({'success': True, 'data': data})
    else:
        return jsonify({'success': False, 'message': 'Item not found.'}), 404

@app.route('/delete_data/<type>/<item_id>', methods=['DELETE'])
def delete_data(type, item_id):
    if type == 'lecturers':
        collection = teachers_collection
    elif type == 'rooms':
        collection = rooms_collection
    elif type == 'departments':
        collection = departments_collection
    elif type == 'courses':
        collection = courses_collection
    else:
        return jsonify({'success': False, 'message': 'Invalid data type.'}), 400
    
    try:
        result = collection.delete_one({'_id': ObjectId(item_id)})
    except InvalidId:
        return jsonify({'success': False, 'message': 'Invalid ID format.'}), 400

    if result.deleted_count == 1:
        return jsonify({'success': True, 'message': f'{type.capitalize()} deleted successfully.'})
    else:
        return jsonify({'success': False, 'message': f'Could not find {type} with ID {item_id}.'}), 404

@app.route('/update_data/<type>/<item_id>', methods=['POST'])
def update_data(type, item_id):
    new_data = request.json
    
    if type == 'lecturers':
        collection = teachers_collection
    elif type == 'rooms':
        collection = rooms_collection
        if 'capacity' in new_data:
            new_data['capacity'] = int(new_data['capacity'])
    elif type == 'departments':
        collection = departments_collection
    elif type == 'courses':
        collection = courses_collection
        if 'department_ids' in new_data:
            new_data['department_ids'] = [ObjectId(did) for did in new_data['department_ids']]
        if 'lecturer_ids' in new_data:
            new_data['lecturer_ids'] = [ObjectId(lid) for lid in new_data['lecturer_ids']]
        if 'number_of_students' in new_data:
            new_data['number_of_students'] = int(new_data['number_of_students'])
        new_data['level'] = new_data.get('level', 'Unknown')
    else:
        return jsonify({'success': False, 'message': 'Invalid data type.'}), 400
    
    try:
        result = collection.update_one({'_id': ObjectId(item_id)}, {'$set': new_data})
    except InvalidId:
        return jsonify({'success': False, 'message': 'Invalid ID format.'}), 400

    if result.matched_count == 1:
        return jsonify({'success': True, 'message': f'{type.capitalize()} updated successfully.'})
    else:
        return jsonify({'success': False, 'message': f'Could not find {type} with ID {item_id}.'}), 404

@app.route('/settings')
def settings():
    return render_template('settings.html', settings=get_settings(), active_page='settings')

@app.route('/update_settings', methods=['POST'])
def update_settings():
    new_settings = request.json
    settings = load_settings()
    settings.update(new_settings)
    save_settings(settings)
    return jsonify({'success': True, 'message': 'Settings updated successfully.'})

@app.route('/save_timetable', methods=['POST'])
def save_timetable():
    try:
        timetable_data = request.json
        if not timetable_data:
            return jsonify({'success': False, 'message': 'Invalid data received.'}), 400

        department_name = timetable_data.get('department_name') or "General Timetable"

        saved_timetables_collection.insert_one({
            'name': f'Timetable for {department_name} ({datetime.now().strftime("%Y-%m-%d %H:%M:%S")})',
            'timetable_data': timetable_data['timetable_data'],
            'department_name': department_name,
            'created_at': datetime.now()
        })
        return jsonify({'success': True, 'message': 'Timetable saved successfully.'})
    
    except Exception as e:
        print(f"Error saving timetable: {e}")
        return jsonify({'success': False, 'message': 'An unexpected server error occurred.'}), 500


@app.route('/generate_timetable')
def generate_timetable_route():
    all_departments_list = list(departments_collection.find())
    all_departments = {str(d['_id']): d['name'] for d in all_departments_list}
    all_teachers = list(teachers_collection.find())
    all_rooms = list(rooms_collection.find())
    all_courses = list(courses_collection.find())

    if not all_courses or not any(c.get('number_of_lectures_per_week', 0) > 0 for c in all_courses):
        flash('Cannot generate a timetable. Please ensure you have added courses with at least one lecture per week.', 'warning')
        return render_template('timetable.html', timetable={}, time_slots=display_time_slots, days=DAYS, departments=all_departments, page_title="Generated Timetable", is_saved=False, active_page='generate_timetable')

    best_individual = backtracking_algorithm(all_courses, all_rooms, all_teachers)
    
    if best_individual is None:
        flash('Could not generate a conflict-free timetable. Please check your data and constraints.', 'error')
        return render_template('timetable.html', timetable={}, time_slots=display_time_slots, days=DAYS, departments=all_departments, page_title="Generated Timetable", is_saved=False, active_page='generate_timetable')

    display_timetable = get_display_timetable(best_individual)
    display_timetable = convert_objectids_to_strings(display_timetable) # Corrected line
    is_saved = False

    return render_template(
        'timetable.html',
        timetable=display_timetable,
        time_slots=display_time_slots,
        days=DAYS,
        departments=all_departments,
        page_title="Generated Timetable",
        is_saved=is_saved,
        active_page='generate_timetable'
    )

@app.route('/timetable/<timetable_id>')
def view_saved_timetable(timetable_id):
    timetable_data_doc = saved_timetables_collection.find_one({'_id': ObjectId(timetable_id)})
    if not timetable_data_doc:
        return "Timetable not found", 404

    display_timetable = timetable_data_doc['timetable_data']
    display_timetable = convert_objectids_to_strings(display_timetable) # Corrected line
    
    all_departments_list = list(departments_collection.find())
    all_departments = {str(d['_id']): d['name'] for d in all_departments_list}
    
    return render_template(
        'timetable.html',
        timetable=display_timetable,
        time_slots=display_time_slots,
        days=DAYS,
        departments=all_departments,
        page_title=timetable_data_doc['name'],
        is_saved=True,
        timetable_id=timetable_id,
        active_page='timetable'
    )

@app.route('/download_timetable/<timetable_id>')
def download_timetable(timetable_id):
    department_id = request.args.get('department_id')
    timetable_data_doc = saved_timetables_collection.find_one({'_id': ObjectId(timetable_id)})
    if not timetable_data_doc:
        return "Timetable not found", 404

    department_name = "General"
    if department_id:
        dept = departments_collection.find_one({'_id': ObjectId(department_id)})
        if dept:
            department_name = dept['name']

    header_text = f"Timetable for {department_name}"
    pdf_buffer = generate_pdf_from_timetable(
        timetable_data_doc['timetable_data'], 
        header_text, 
        department_name=department_name if department_id else None
    )
    
    filename = f"timetable_{department_name.replace(' ', '_')}_{timetable_id}.pdf"
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )
    
@app.route('/get_departments', methods=['GET'])
def get_departments():
    departments = list(departments_collection.find())
    departments = convert_objectids_to_strings(departments)
    return jsonify(departments)

@app.route('/get_lecturers', methods=['GET'])
def get_lecturers():
    lecturers = list(teachers_collection.find())
    lecturers = convert_objectids_to_strings(lecturers)
    return jsonify(lecturers)

if __name__ == '__main__':
    app.run(debug=True)