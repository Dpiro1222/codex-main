# Tipping System

This repository contains a simple proof-of-concept for sending tips to service workers from a smartphone. It consists of a small Flask backend and dynamically generated HTML pages that are mobile friendly.

## Setup

1. Install Python dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

2. Run the server:
   ```bash
   python backend/app.py
   ```

3. Register a worker by visiting `http://localhost:5000/worker/register` and follow the prompts.
4. After registering, you'll be redirected to a tipping page that can be shared with customers.

Tips are stored in a local SQLite database `tips.db`.
