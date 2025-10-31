Flask frontend for TeachingKG

How to run (development):

1. Create a virtual environment in this folder and activate it (Windows PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Set environment variables if needed (optional):

```powershell
# Example (change credentials as needed)
$env:NEO4J_URI = 'bolt://localhost:7687'
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASSWORD = 'KG_edu_1'
$env:FLASK_SECRET_KEY = 'replace-me'
```

3. Run the app:

```powershell
python main_extended_alpha_version.py
```

Notes:
- This app uses Neo4j to store users and courses. Ensure Neo4j is running and reachable.
- The UI was migrated from Streamlit to Flask with Bootstrap templates. It's intentionally minimal to keep code compact.# Use-case and data collection interface

Interface is in alpha version, and is generated with [Streamlit](https://streamlit.io)

A neo4j database is required for storing the information collected on the interface. 

DB password should be modified in the function `init_neo4j_connection()` accordingly.

## Run the code 
From the root directory with `streamlit run main_extended_alpha_version.py`
