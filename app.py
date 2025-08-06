from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
from pymongo import MongoClient
from bson.objectid import ObjectId
import os
import random
from datetime import time, datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Any
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from io import BytesIO
import json

app = Flask(__name__)

# Replace with your actual MongoDB URI
MONGO_URI = "mongodb+srv://fortunedwards:oselumese@universityschedulerclus.rqbvhmw.mongodb.net/?retryWrites=true&w=majority&appName=UniversitySchedulerCluster"
client = MongoClient(MONGO_URI)
db = client['university_scheduler']

teachers_collection = db['teachers']
rooms_collection = db['rooms']
courses_collection = db['courses']
saved_timetables_collection = db['saved_timetables']
departments_collection = db['departments'] # New collection for departments

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
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("Warning: settings.json is corrupted. Using default settings.")
            return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

SETTINGS = load_settings()
if not os.path.exists(SETTINGS_FILE) or 'ga_population_size' not in SETTINGS:
    save_settings(DEFAULT_SETTINGS)
    SETTINGS = DEFAULT_SETTINGS

# --- Genetic Algorithm Configuration ---
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
TIME_SLOTS = [
    time(8, 0), time(9, 0), time(10, 0), time(11, 0), time(12, 0),
    time(13, 0), time(14, 0), time(15, 0), time(16, 0)
]
ALL_SLOTS = [(day, t) for day in DAYS for t in TIME_SLOTS]
TIME_SLOT_INDICES = {t: i for i, t in enumerate(TIME_SLOTS)}

# --- Global variable to store the generated timetable ---
best_timetable_result = []

# --- Helper function to fix the JSON serialization issues ---
def json_serializable_timetable(timetable):
    """Converts ObjectId and time objects in the timetable to strings."""
    serialized_timetable = []
    for event in timetable:
        serialized_event = event.copy()
        
        if 'course_id' in serialized_event and isinstance(serialized_event['course_id'], ObjectId):
            serialized_event['course_id'] = str(serialized_event['course_id'])
        if 'lecturer_ids' in serialized_event and isinstance(serialized_event['lecturer_ids'], list):
            serialized_event['lecturer_ids'] = [str(lid) for lid in serialized_event['lecturer_ids']]
        if 'room_id' in serialized_event and isinstance(serialized_event['room_id'], ObjectId):
            serialized_event['room_id'] = str(serialized_event['room_id'])
        if 'time' in serialized_event and isinstance(serialized_event['time'], time):
            serialized_event['time'] = serialized_event['time'].isoformat()
            
        serialized_timetable.append(serialized_event)
    return serialized_timetable

# --- Function to format timetable data for the template ---
def generate_timetable_dict(timetable_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Formats the raw timetable data into a dictionary suitable for the template.
    
    Args:
        timetable_list: A list of dictionaries, where each dictionary represents a course.
        
    Returns:
        A dictionary with time slots as keys and a nested dictionary for each day.
    """
    days = DAYS
    time_slots = TIME_SLOTS
    
    formatted_timetable = {time: {day: None for day in days} for time in time_slots}
    
    for item in timetable_list:
        time = item['time']
        day = item['day']
        if time in formatted_timetable and day in formatted_timetable[time]:
            # This is the key change. We now store the list of events for that slot,
            # as multiple departments might have a class at the same time.
            if not formatted_timetable[time][day]:
                formatted_timetable[time][day] = []
            formatted_timetable[time][day].append(item)
    
    return formatted_timetable

# --- Flask Routes ---
@app.route('/')
def index():
    saved_timetables = list(saved_timetables_collection.find().sort("timestamp", -1))
    return render_template('index.html', saved_timetables=saved_timetables, active_page='index')

@app.route('/data_management', methods=['GET'])
def data_management():
    tab_type = request.args.get('type', 'lecturers')
    lecturers = list(teachers_collection.find())
    rooms = list(rooms_collection.find())
    courses = list(courses_collection.find())
    departments = list(departments_collection.find()) # Fetch departments
    
    lecturer_map = {str(t['_id']): t['name'] for t in lecturers}

    for course in courses:
        lecturer_ids = course.get('lecturer_ids')
        if lecturer_ids:
            lecturer_names = [lecturer_map.get(str(lid), 'Unknown Lecturer') for lid in lecturer_ids]
        else:
            lecturer_id = course.get('teacher_id')
            lecturer_names = [lecturer_map.get(str(lecturer_id), 'Unknown Lecturer')] if lecturer_id else []
        course['lecturer_names'] = ", ".join(lecturer_names)
        course['departments_str'] = ", ".join(course.get('departments', []))

    all_departments = [d['name'] for d in departments]

    return render_template(
        'data_management.html', 
        lecturers=lecturers, 
        rooms=rooms, 
        courses=courses,
        departments=departments, # Pass departments to the template
        active_tab=tab_type,
        active_page='data_management',
        all_departments=all_departments
    )

@app.route('/settings', methods=['GET'])
def settings():
    return render_template('settings.html', settings=SETTINGS, active_page='settings')

@app.route('/update_settings', methods=['POST'])
def update_settings():
    global SETTINGS
    try:
        data = request.json
        if 'ga_population_size' in data:
            SETTINGS['ga_population_size'] = int(data.get('ga_population_size'))
            SETTINGS['ga_generations'] = int(data.get('ga_generations'))
            SETTINGS['ga_mutation_rate'] = float(data.get('ga_mutation_rate'))
            SETTINGS['ga_crossover_rate'] = float(data.get('ga_crossover_rate'))
        
        if 'max_lecturer_hours_per_week' in data:
            SETTINGS['max_lecturer_hours_per_week'] = int(data.get('max_lecturer_hours_per_week'))
        
        save_settings(SETTINGS)
        return jsonify(success=True, message="Settings updated successfully!")
    except (ValueError, TypeError):
        return jsonify(success=False, message="Invalid input. Please enter valid numbers."), 400
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

@app.route('/add_teacher', methods=['POST'])
def add_teacher():
    name = request.form.get('name')
    if name:
        teacher = {'name': name}
        teachers_collection.insert_one(teacher)
        return jsonify(success=True, message=f"Lecturer '{name}' added successfully!")
    return jsonify(success=False, message="Invalid lecturer name"), 400

@app.route('/add_room', methods=['POST'])
def add_room():
    name = request.form.get('name')
    capacity = request.form.get('capacity', type=int)
    if name and capacity:
        room = {'name': name, 'capacity': capacity}
        rooms_collection.insert_one(room)
        return jsonify(success=True, message=f"Room '{name}' added successfully!")
    return jsonify(success=False, message="Invalid room details"), 400

@app.route('/add_department', methods=['POST'])
def add_department():
    name = request.form.get('name')
    if name:
        department = {'name': name}
        departments_collection.insert_one(department)
        return jsonify(success=True, message=f"Department '{name}' added successfully!")
    return jsonify(success=False, message="Invalid department name"), 400

@app.route('/add_course', methods=['POST'])
def add_course():
    name = request.form.get('name')
    lecturer_names = request.form.getlist('lecturers')
    lectures_per_week = request.form.get('lectures_per_week', type=int)
    level = request.form.get('level')
    departments = request.form.getlist('departments')
    student_count = request.form.get('student_count', type=int)
    
    if name and lecturer_names and lectures_per_week and level and student_count and departments:
        lecturer_ids = []
        for lecturer_name in lecturer_names:
            lecturer = teachers_collection.find_one({'name': lecturer_name})
            if lecturer:
                lecturer_ids.append(lecturer['_id'])
        
        if lecturer_ids:
            course = {
                'name': name,
                'lecturer_ids': lecturer_ids,
                'lectures_per_week': lectures_per_week,
                'level': level,
                'departments': departments,
                'student_count': student_count
            }
            courses_collection.insert_one(course)
            return jsonify(success=True, message=f"Course '{name}' added successfully!")
    
    return jsonify(success=False, message="Invalid course details"), 400

@app.route('/delete/<collection>/<item_id>', methods=['DELETE'])
def delete_item(collection, item_id):
    try:
        if collection == 'teachers':
            teachers_collection.delete_one({'_id': ObjectId(item_id)})
        elif collection == 'rooms':
            rooms_collection.delete_one({'_id': ObjectId(item_id)})
        elif collection == 'courses':
            courses_collection.delete_one({'_id': ObjectId(item_id)})
        elif collection == 'saved_timetables':
            saved_timetables_collection.delete_one({'_id': ObjectId(item_id)})
        elif collection == 'departments':
            departments_collection.delete_one({'_id': ObjectId(item_id)})
        else:
            return jsonify(success=False, message="Invalid collection"), 400
        
        return jsonify(success=True, message=f"Item deleted successfully from {collection}!")
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

@app.route('/update/<collection>/<item_id>', methods=['POST'])
def update_item(collection, item_id):
    try:
        data = request.json
        if collection == 'teachers':
            teachers_collection.update_one({'_id': ObjectId(item_id)}, {'$set': data})
        elif collection == 'rooms':
            rooms_collection.update_one({'_id': ObjectId(item_id)}, {'$set': data})
        elif collection == 'courses':
            if 'lecturer_names' in data:
                lecturer_ids = [teachers_collection.find_one({'name': name})['_id'] for name in data['lecturer_names']]
                data['lecturer_ids'] = lecturer_ids
                del data['lecturer_names']
            courses_collection.update_one({'_id': ObjectId(item_id)}, {'$set': data})
        else:
            return jsonify(success=False, message="Invalid collection"), 400
        
        return jsonify(success=True, message=f"Item updated successfully in {collection}!")
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

@app.route('/check_conflict', methods=['POST'])
def check_conflict():
    course_name = request.json.get('course_name')
    lecturer_name = request.json.get('lecturer_name')
    lectures_per_week = int(request.json.get('lectures_per_week'))
    level = request.json.get('level')
    
    lecturer = teachers_collection.find_one({'name': lecturer_name})
    if not lecturer:
        return jsonify({'conflict': True, 'message': 'Lecturer not found. Please add the lecturer first.'})
    
    existing_courses = list(courses_collection.find({'lecturer_ids': lecturer['_id']}))
    total_lectures = sum(c['lectures_per_week'] for c in existing_courses) + lectures_per_week

    if total_lectures > SETTINGS['max_lecturer_hours_per_week']:
        return jsonify({'conflict': True, 'message': f'Warning: {lecturer_name} is already assigned a high number of classes ({total_lectures-lectures_per_week} classes). This may cause scheduling conflicts.'})
    
    return jsonify({'conflict': False, 'message': 'No immediate conflicts detected.'})

# --- Genetic Algorithm Core Functions ---
def get_all_data():
    """Fetches all necessary data from the database."""
    return {
        'teachers': list(teachers_collection.find()),
        'rooms': list(rooms_collection.find()),
        'courses': list(courses_collection.find())
    }

def create_individual(data):
    """Creates a single, random timetable (a 'chromosome')."""
    individual = []
    rooms = data['rooms']
    all_courses = data['courses']

    for course in all_courses:
        for _ in range(course['lectures_per_week']):
            time_slot = random.choice(ALL_SLOTS)
            room = random.choice(rooms)
            
            lecturer_ids = course.get('lecturer_ids', [])
            
            event = {
                'course_id': course['_id'],
                'lecturer_ids': lecturer_ids,
                'level': course['level'],
                'departments': course.get('departments', []),  # FIX: Default to empty list
                'day': time_slot[0],
                'time': time_slot[1],
                'room_id': room['_id'],
                'student_count': course.get('student_count', 0)
            }
            individual.append(event)
    return individual

def calculate_fitness(individual, data):
    """Calculates the fitness score of a timetable. Higher score is better."""
    score = 0
    lecturer_schedule = {}
    room_schedule = {}
    level_schedule = {}
    
    lecturer_daily_schedules = defaultdict(list)
    level_daily_schedules = defaultdict(list)
    lecturer_weekly_hours = defaultdict(int)

    room_map = {r['_id']: r['capacity'] for r in data['rooms']}
    
    for event in individual:
        day = event['day']
        t = event['time']
        
        lecturer_ids = event.get('lecturer_ids', [])

        for lecturer_id in lecturer_ids:
            lecturer_key = (lecturer_id, day, t)
            if lecturer_key in lecturer_schedule:
                score -= 100
            else:
                lecturer_schedule[lecturer_key] = True
                lecturer_daily_schedules[lecturer_id, day].append(t)
                lecturer_weekly_hours[lecturer_id] += 1

        room_key = (event['room_id'], day, t)
        if room_key in room_schedule:
            score -= 100
        else:
            room_schedule[room_key] = True

        level = event['level']
        level_key = (level, day, t)
        if level_key in level_schedule:
            score -= 100
        else:
            level_schedule[level_key] = True
            level_daily_schedules[level, day].append(t)
            
        room_capacity = room_map.get(event['room_id'], 0)
        if event['student_count'] > room_capacity:
            score -= 100

    if 'max_lecturer_hours_per_week' in SETTINGS:
        for lecturer_id, hours in lecturer_weekly_hours.items():
            if hours > SETTINGS['max_lecturer_hours_per_week']:
                score -= 1000
    
    for lecturer_day_key, times in lecturer_daily_schedules.items():
        times.sort(key=lambda x: TIME_SLOT_INDICES[x])
        
        for i in range(len(times) - 1):
            current_time_index = TIME_SLOT_INDICES[times[i]]
            next_time_index = TIME_SLOT_INDICES[times[i+1]]
            
            if next_time_index == current_time_index + 1:
                score -= 5
    
    for level_day_key, times in level_daily_schedules.items():
        times.sort(key=lambda x: TIME_SLOT_INDICES[x])

        for i in range(len(times) - 1):
            current_time_index = TIME_SLOT_INDICES[times[i]]
            next_time_index = TIME_SLOT_INDICES[times[i+1]]

            gap = next_time_index - (current_time_index + 1)
            if gap > 0:
                score -= (gap * 2)
    
    return score

def select_parents(population, data):
    """Selects two parent timetables using a simple tournament selection."""
    sorted_population = sorted(population, key=lambda x: calculate_fitness(x, data), reverse=True)
    return sorted_population[0], sorted_population[1]

def crossover(parent1, parent2):
    """Combines two parent timetables to create a child timetable."""
    if random.random() < SETTINGS['ga_crossover_rate']:
        crossover_point = random.randint(1, len(parent1) - 1)
        child = parent1[:crossover_point] + parent2[crossover_point:]
        return child
    return parent1

def mutate(individual, data):
    """Randomly mutates a timetable to introduce new variations."""
    if random.random() < SETTINGS['ga_mutation_rate']:
        mutation_point = random.randint(0, len(individual) - 1)
        
        rooms = data['rooms']
        new_time_slot = random.choice(ALL_SLOTS)
        new_room = random.choice(rooms)

        if random.random() > 0.5:
            individual[mutation_point]['day'] = new_time_slot[0]
            individual[mutation_point]['time'] = new_time_slot[1]
        else:
            individual[mutation_point]['room_id'] = new_room['_id']
    return individual

@app.route('/generate_timetable', methods=['GET'])
def generate_timetable():
    global best_timetable_result
    data = get_all_data()

    if not data['teachers'] or not data['rooms'] or not data['courses']:
        return render_template('timetable.html', timetable={}, departments=[], message="Please add lecturers, rooms, and courses first.", active_page='index')

    lecturer_map = {str(t['_id']): t['name'] for t in data['teachers']}
    room_map = {str(r['_id']): r['name'] for r in data['rooms']}
    course_map = {str(c['_id']): c['name'] for c in data['courses']}

    population = [create_individual(data) for _ in range(SETTINGS['ga_population_size'])]
    
    best_timetable = None
    best_fitness = -100000 
    
    for generation in range(SETTINGS['ga_generations']):
        fitness_scores = [(ind, calculate_fitness(ind, data)) for ind in population]
        fittest_individual, current_best_fitness = max(fitness_scores, key=lambda item: item[1])
        
        if current_best_fitness > best_fitness:
            best_fitness = current_best_fitness
            best_timetable = fittest_individual

        if best_fitness == 0:
            print(f"Solution found in generation {generation}!")
            break
            
        new_population = [best_timetable]
        
        while len(new_population) < SETTINGS['ga_population_size']:
            parent1, parent2 = select_parents(population, data)
            child = crossover(parent1, parent2)
            child = mutate(child, data)
            new_population.append(child)
            
        population = new_population

    display_timetable = []
    if best_timetable:
        for event in best_timetable:
            lecturer_ids = event.get('lecturer_ids', [])
            lecturer_names = [lecturer_map.get(str(lid), 'Unknown') for lid in lecturer_ids]
            
            event_for_display = {
                'course_title': course_map.get(str(event['course_id'])),
                'lecturer_names': ", ".join(lecturer_names),
                'room_name': room_map.get(str(event['room_id'])),
                'level': event['level'],
                'day': event['day'],
                'time': event['time'],
                'departments': event.get('departments', []), # FIX: Default to empty list
                'lecturer_ids': [str(lid) for lid in lecturer_ids]
            }
            display_timetable.append(event_for_display)

    best_timetable_result = display_timetable

    formatted_timetable = generate_timetable_dict(display_timetable)
    unique_departments = sorted(list(set(d for event in display_timetable for d in event.get('departments', []))))


    return render_template(
        'timetable.html', 
        timetable=formatted_timetable, 
        days=DAYS, 
        time_slots=TIME_SLOTS,
        departments=unique_departments,
        page_title='Generated Timetable',
        active_page='index'
    )
    
@app.route('/lecturer_timetable/<teacher_id>')
def lecturer_timetable(teacher_id: str):
    lecturer = teachers_collection.find_one({'_id': ObjectId(teacher_id)})
    lecturer_name = lecturer['name'] if lecturer else 'Unknown Lecturer'
    
    if not best_timetable_result:
        return render_template('timetable.html', timetable={}, message="Please generate a timetable first.", active_page='index')

    lecturer_courses = [
        event for event in best_timetable_result
        if teacher_id in event.get('lecturer_ids', [])
    ]

    formatted_timetable = generate_timetable_dict(lecturer_courses)
    
    return render_template(
        'timetable.html',
        timetable=formatted_timetable,
        days=DAYS,
        time_slots=TIME_SLOTS,
        page_title=f"Timetable for {lecturer_name}",
        active_page='index'
    )

@app.route('/download_timetable/<department_name>')
def download_timetable(department_name):
    if not best_timetable_result:
        return "Please generate a timetable first via the main page."

    department_timetable = [event for event in best_timetable_result if department_name in event.get('departments', [])]
    
    if not department_timetable:
        return f"No timetable found for department: {department_name}", 404

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    story = []
    
    styles = getSampleStyleSheet()
    story.append(Paragraph(f"Timetable for {department_name} Department", styles['h1']))
    story.append(Paragraph("<br/>", styles['Normal']))

    header_data = ['Time'] + DAYS
    table_data = [header_data]
    
    time_slots = TIME_SLOTS
    
    timetable_dict = generate_timetable_dict(department_timetable)
    
    for slot in time_slots:
        row = [slot.strftime('%H:%M')]
        for day in DAYS:
            events_for_slot = timetable_dict.get(slot, {}).get(day)
            if events_for_slot:
                cell_content = ""
                for event in events_for_slot:
                    cell_content += f"{event['course_title']}<br/>({event['lecturer_names']})<br/>Room: {event['room_name']}<br/>"
                row.append(Paragraph(cell_content, styles['Normal']))
            else:
                row.append('')
        table_data.append(row)
    
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ])
    
    col_widths = [1.5 * 72] + [1.3 * 72] * 5
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(table_style)
    story.append(table)
    
    doc.build(story)
    
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'timetable_{department_name}.pdf',
        mimetype='application/pdf'
    )

@app.route('/save_timetable', methods=['POST'])
def save_timetable():
    global best_timetable_result
    if not best_timetable_result:
        return jsonify({'success': False, 'message': 'No timetable to save.'})

    saved_data = {
        'timetable': [
            {
                **event,
                'time': event['time'].isoformat(),
            }
            for event in best_timetable_result
        ],
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        saved_timetables_collection.insert_one(saved_data)
        return jsonify({'success': True, 'message': 'Timetable saved successfully!'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/view_saved_timetable/<timetable_id>')
def view_saved_timetable(timetable_id):
    try:
        saved_timetable_doc = saved_timetables_collection.find_one({'_id': ObjectId(timetable_id)})
        
        if not saved_timetable_doc:
            return "Timetable not found.", 404
        
        timetable_for_display = [
            {
                **event,
                'time': time.fromisoformat(event['time'])
            }
            for event in saved_timetable_doc['timetable']
        ]

        formatted_timetable = generate_timetable_dict(timetable_for_display)
        unique_departments = sorted(list(set(d for event in timetable_for_display for d in event.get('departments', []))))
        
        return render_template(
            'timetable.html',
            timetable=formatted_timetable,
            days=DAYS,
            time_slots=TIME_SLOTS,
            departments=unique_departments,
            page_title=f"Saved Timetable ({saved_timetable_doc['timestamp'].split('T')[0]})",
            is_saved=True,
            active_page='index'
        )

    except Exception as e:
        return f"An error occurred: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True)