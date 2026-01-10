The GitHub repository of the "Teaching Knowledge Graph for Knowledge Graphs Education" paper.

License [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/)


# TeachingKG Interface

Interactive tools for exploring and authoring courses that leverage the Teaching Knowledge Graph. The project combines RDF-backed read-only views with data entry flows that persist to Neo4j, helping educators compare existing curricula, remix catalog content, and publish new offerings.

## Project Overview

- **Flask application** in `interface/` renders the authoring experience, catalogue views, and supporting pages.
- **RDF knowledge graph** (N-Triples) in `mapping_rules/output.nt` powers the read-only browse experiences.
- **Neo4j persistence** is used for authentication and storing courses created through the UI (optional for catalogue-only use).
- **Static assets** (Bootstrap/Bootswatch, custom CSS, images) are bundled under `interface/static/` and referenced from the shared templates in `interface/templates/`.

## Repository Structure

- `interface/main_extended_alpha_version.py` — main Flask app with routes for browse, create, complete, and admin flows.
- `interface/templates/` — Jinja templates (course catalogue, create/complete builders, shared fragments).
- `interface/static/` — CSS, icons, and images.
- `mapping_rules/` — RDF mappings, generated triples (`output.nt`), and documentation.
- `api/` and `vercel.json` — serverless adapter used when deploying to Vercel.
- `requirements.txt` — dependency aggregation (delegates to `interface/requirements.txt`).

## Prerequisites

- Python 3.10 or newer.
- Optional: a running Neo4j instance (4.x/5.x, Bolt protocol) if you plan to log in or persist authored courses.
- Optional: `interface/.env` file for local secrets (see below).

## Local Setup

1. Clone the repository and open a terminal in the root directory.
2. Create a virtual environment (recommended):

	```bash
	python -m venv .venv
	.venv\Scripts\activate
	```

3. Install dependencies:

	```bash
	pip install -r interface/requirements.txt
	```

4. (Optional) Provide secrets in `interface/.env`:

	```
	FLASK_SECRET_KEY=dev-secret
	NEO4J_URI=bolt://localhost:7687
	NEO4J_USER=neo4j
	NEO4J_PASSWORD=[your_Neo4j_password_here]
	```

	If Neo4j variables are omitted or the database is unreachable, catalogue browsing will still work, but authentication and course creation will be disabled.

## Running the App Locally

From the `interface/` directory (or from project root using the module path), launch the Flask server:

```bash
python interface/main_extended_alpha_version.py
```

The development server listens on `http://127.0.0.1:5000` by default. Override the port by setting the `PORT` environment variable before starting the app. Set `FLASK_DEBUG=1` to enable debug mode.

### Useful Routes

- `/` → Landing page and quick links.
- `/courses` → Knowledge graph catalogue with search and detail views.
- `/create_course` → Course builder that seeds content from KG search results.
- `/complete_course` → Curriculum completion flow reusing KG search (requires Neo4j for persistence).
- `/add_course` → Admin form for capturing full course metadata (login required).

## Vercel Deployment

This repository includes configuration for serverless hosting via Vercel.

### Included Files

- `vercel.json` routes all requests to the Python function and declares static/template assets for bundling.
- `api/index.py` exposes the Flask `app` to `@vercel/python` runtime.
- Root `requirements.txt` and `api/requirements.txt` install dependencies defined in `interface/requirements.txt` during build.

### Required Environment Variables

Configure these in Vercel Project Settings → Environment Variables:

- `FLASK_SECRET_KEY`
- `NEO4J_URI` (e.g., `bolt+s://host:7687`)
- `NEO4J_USER`
- `NEO4J_PASSWORD`

Without Neo4j connectivity only the RDF-backed routes (`/`, `/courses`, `/courses/<id>`) will function.

### Deployment Steps

1. Push your changes to GitHub.
2. Import or link the repository in Vercel.
3. Set the environment variables listed above.
4. Trigger a deployment. Vercel will build with `@vercel/python` (Python 3.11), install dependencies, and serve all routes through `api/index.py` while bundling templates and static assets declared in `vercel.json`.

