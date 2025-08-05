import sqlite3
from flask import Flask, request, g, render_template_string, redirect, url_for
from datetime import datetime

app = Flask(__name__)
DATABASE = 'tips.db'

# HTML template for worker tipping page
WORKER_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tip {{ worker['name'] }}</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 2em; }
        .container { max-width: 400px; margin: auto; }
        input, textarea { width: 100%; padding: .5em; margin-bottom: 1em; }
        button { padding: .5em 1em; }
    </style>
</head>
<body>
<div class="container">
    <h2>Tip {{ worker['name'] }}</h2>
    <form action="{{ url_for('tip', worker_id=worker['id']) }}" method="post">
        <label>Amount:</label>
        <input type="number" name="amount" min="1" step="0.01" required>
        <label>Message:</label>
        <textarea name="message" rows="3"></textarea>
        <button type="submit">Send Tip</button>
    </form>
</div>
</body>
</html>
"""

# HTML template for simple worker registration
REGISTER_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register Worker</title>
</head>
<body>
    <h2>Register Worker</h2>
    <form action="/worker/register" method="post">
        <label>Name:</label>
        <input type="text" name="name" required>
        <button type="submit">Register</button>
    </form>
</body>
</html>
"""

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.executescript(f.read())
        db.commit()

@app.route('/worker/register', methods=['GET', 'POST'])
def register_worker():
    if request.method == 'POST':
        name = request.form['name']
        db = get_db()
        cur = db.execute('INSERT INTO workers (name) VALUES (?)', (name,))
        db.commit()
        worker_id = cur.lastrowid
        return redirect(url_for('worker_page', worker_id=worker_id))
    return REGISTER_PAGE

@app.route('/worker/<int:worker_id>')
def worker_page(worker_id):
    db = get_db()
    worker = db.execute('SELECT * FROM workers WHERE id=?', (worker_id,)).fetchone()
    if not worker:
        return 'Worker not found', 404
    return render_template_string(WORKER_PAGE, worker=worker)

@app.route('/tip/<int:worker_id>', methods=['POST'])
def tip(worker_id):
    db = get_db()
    worker = db.execute('SELECT * FROM workers WHERE id=?', (worker_id,)).fetchone()
    if not worker:
        return 'Worker not found', 404
    amount = request.form.get('amount') or request.json.get('amount')
    message = request.form.get('message') or request.json.get('message', '')
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return 'Invalid amount', 400
    db.execute(
        'INSERT INTO tips (worker_id, amount, message, time) VALUES (?, ?, ?, ?)',
        (worker_id, amount, message, datetime.utcnow())
    )
    db.commit()
    return redirect(url_for('worker_page', worker_id=worker_id))

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
