from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from neo4j import GraphDatabase
from werkzeug.security import generate_password_hash, check_password_hash
import os
from dotenv import dotenv_values
from datetime import datetime, timezone
from rdflib import Graph as RDFGraph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, XSD
import difflib
import re
from urllib.parse import urlsplit, urlunsplit, quote, unquote

# Prefer reading secrets directly from the interface/.env file (not the OS environment).
# This returns a dict of values in the .env file. If a key is missing there we fall back to
# a sensible default (but we do NOT prefer OS env vars over the .env file per your request).
env_path = os.path.join(os.path.dirname(__file__), '.env')
env_values = {}
try:
    env_values = dotenv_values(env_path) or {}
except Exception:
    env_values = {}

# Simple Flask app to replace the previous Streamlit UI. This file implements:
# - Flask routes for the previously separate Streamlit tabs (Add, Create, Complete, About, Licensing, Examples)
# - User registration and login that stores users in Neo4j
# - Persistence of courses in Neo4j and linking courses to the creating user

app = Flask(__name__)
# Read secret key from .env file first, fallback to a dev value if missing
app.config['SECRET_KEY'] = env_values.get('FLASK_SECRET_KEY') or 'dev-secret-change-in-prod'


# Namespaces for RDF graph
SCHEMA = Namespace("http://schema.org/")
COURSES = Namespace("https://w3id.org/def/courses#")
EDUCOR = Namespace("https://github.com/tibonto/educor#")

# Load RDF graph from mapping_rules/output.nt (N-Triples)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
RDF_FILE = os.path.join(REPO_ROOT, 'mapping_rules', 'output.nt')
_rdf_graph = None
try:
    g = RDFGraph()
    if os.path.exists(RDF_FILE):
        g.parse(RDF_FILE, format='nt')
        _rdf_graph = g
    else:
        _rdf_graph = None
except Exception:
    _rdf_graph = None


def _first_literal(s, p):
    if not _rdf_graph:
        return None
    for o in _rdf_graph.objects(s, p):
        if isinstance(o, Literal):
            return str(o)
        # if it's a URI, try to find its name
        if isinstance(o, URIRef):
            name = _first_literal(o, SCHEMA.name)
            if name:
                return name
    return None


def _bool_value(s, p):
    if not _rdf_graph:
        return None
    for o in _rdf_graph.objects(s, p):
        if isinstance(o, Literal):
            if o.datatype == XSD.boolean:
                return bool(str(o).lower() == 'true')
            # Some values might be 'TRUE'/'FALSE' as strings
            val = str(o).strip().lower()
            if val in ('true', '1'):
                return True
            if val in ('false', '0'):
                return False
    return None


def _get_level_value(subject):
    """Return a readable educational level value from triples.
    Prefers a literal; if URI, tries schema:name, else falls back to URI string.
    """
    if not _rdf_graph:
        return None
    for o in _rdf_graph.objects(subject, SCHEMA.educationalLevel):
        if isinstance(o, Literal):
            return str(o)
        if isinstance(o, URIRef):
            name = _first_literal(o, SCHEMA.name)
            return name or str(o)
    return None


def _short_level_label(val: str) -> str:
    if not val:
        return ''
    s = str(val)
    # take last fragment if URI-like
    if '/' in s or '#' in s:
        s = s.split('#')[-1].split('/')[-1]
    low = s.lower()
    # normalize common categories
    if any(k in low for k in ['phd', 'doctoral', 'doctorate', 'dphil']):
        return 'PhD'
    if any(k in low for k in ['master', 'msc', 'm.sc', 'gradua']):
        return 'Master'
    if any(k in low for k in ['bachelor', 'undergrad', 'bsc', 'b.sc', 'ba']):
        return 'Bachelor'
    if any(k in low for k in ['high', 'secondary']):
        return 'HS'
    if any(k in low for k in ['diploma', 'certificate', 'cert']):
        return 'Cert'
    if 'associate' in low:
        return 'Associate'
    # fallback: split camel case and truncate
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
    return (s[:12] + '…') if len(s) > 13 else s


def list_courses_from_rdf():
    if not _rdf_graph:
        return []
    courses = []
    for course in _rdf_graph.subjects(RDF.type, SCHEMA.Course):
        name = _first_literal(course, SCHEMA.name)
        url = _first_literal(course, SCHEMA.url)
        # Providers (kept for detail view, but not shown in list by default)
        providers = []
        seen = set()
        for pred in (SCHEMA.provider, COURSES.responsibleEntity):
            for p in _rdf_graph.objects(course, pred):
                pid = str(p)
                if pid in seen:
                    continue
                seen.add(pid)
                ptype = 'Organization' if ((p, RDF.type, SCHEMA.EducationalOrganization) in _rdf_graph or (p, RDF.type, SCHEMA.CollegeOrUniversity) in _rdf_graph) else ('Person' if (p, RDF.type, SCHEMA.Person) in _rdf_graph else None)
                providers.append({
                    'uri': pid,
                    'name': _first_literal(p, SCHEMA.name) or pid,
                    'type': ptype
                })

        # Topic and skill counts for compact list view
        topic_count = sum(1 for _ in _rdf_graph.objects(course, SCHEMA.teaches))
        skill_seen = set()
        for t in _rdf_graph.objects(course, SCHEMA.teaches):
            for s in _rdf_graph.subjects(EDUCOR.requiresKnowledge, t):
                for c in _rdf_graph.objects(s, COURSES.skillRequired):
                    skill_seen.add(str(c))
            for s in _rdf_graph.subjects(SCHEMA.teaches, t):
                for c in _rdf_graph.objects(s, COURSES.skillRequired):
                    skill_seen.add(str(c))
        skills_count = len(skill_seen)

        courses.append({
            'uri': str(course),
            'name': name,
            'url': url,
            'providers': providers,
            'topic_count': topic_count,
            'skills_count': skills_count,
        })
    courses.sort(key=lambda c: (c['name'] or '').lower())
    return courses


def course_detail_from_rdf(course_id_or_name: str):
    if not _rdf_graph:
        return None
    target = None
    # attempt to resolve by URI
    if course_id_or_name and (course_id_or_name.startswith('http') or course_id_or_name.lower().startswith('http%3a') or course_id_or_name.lower().startswith('https%3a')):
        # Try multiple normalization variants to match graph URIs exactly
        variants = []
        raw = course_id_or_name
        variants.append(raw)
        try:
            variants.append(unquote(raw))
            variants.append(unquote(unquote(raw)))
        except Exception:
            pass
        # Attempt direct URIRef match, then with normalized path encoding
        for v in variants:
            try:
                cand = URIRef(v)
                if (cand, RDF.type, SCHEMA.Course) in _rdf_graph:
                    target = cand
                    break
                parts = urlsplit(v)
                # If path contains spaces, encode them; if it contains %25 patterns, unquote once then encode
                path_in = parts.path
                if '%25' in path_in:
                    try:
                        path_in = unquote(path_in)
                    except Exception:
                        pass
                path_q = quote(path_in, safe='/:@&+?$,;=%')
                query_q = quote(parts.query, safe='=:&+/,%')
                frag_q = quote(parts.fragment, safe='=:&+/,%')
                uri_norm = urlunsplit((parts.scheme, parts.netloc, path_q, query_q, frag_q))
                cand2 = URIRef(uri_norm)
                if (cand2, RDF.type, SCHEMA.Course) in _rdf_graph:
                    target = cand2
                    break
            except Exception:
                continue
        if not target:
            # Fallback: scan all course subjects comparing to each variant and their unquoted forms
            for s in _rdf_graph.subjects(RDF.type, SCHEMA.Course):
                s_str = str(s)
                for v in variants:
                    if s_str == v or unquote(s_str) == v:
                        target = s
                        break
                if target:
                    break
    else:
        # resolve by name
        for s in _rdf_graph.subjects(RDF.type, SCHEMA.Course):
            if _first_literal(s, SCHEMA.name) == course_id_or_name:
                target = s
                break
    if not target:
        return None

    name = _first_literal(target, SCHEMA.name)
    url = _first_literal(target, SCHEMA.url)

    providers = []
    seen = set()
    for pred in (SCHEMA.provider, COURSES.responsibleEntity):
        for p in _rdf_graph.objects(target, pred):
            pid = str(p)
            if pid in seen:
                continue
            seen.add(pid)
            ptype = 'Organization' if ((p, RDF.type, SCHEMA.EducationalOrganization) in _rdf_graph or (p, RDF.type, SCHEMA.CollegeOrUniversity) in _rdf_graph) else ('Person' if (p, RDF.type, SCHEMA.Person) in _rdf_graph else None)
            providers.append({
                'uri': pid,
                'name': _first_literal(p, SCHEMA.name) or pid,
                'type': ptype,
                'location': _first_literal(p, SCHEMA.location) if ptype == 'Organization' else None,
                'email': _first_literal(p, SCHEMA.email) if ptype == 'Person' else None,
            })

    topics = []
    level_counts = {}
    theoretical_count = 0
    practical_count = 0
    for t in _rdf_graph.objects(target, SCHEMA.teaches):
        tname = _first_literal(t, SCHEMA.name) or str(t)
        theoretical = _bool_value(t, COURSES.theoreticalTopic)
        main = _bool_value(t, COURSES.mainTopic)
        level_raw = _get_level_value(t)
        level = _short_level_label(level_raw) if level_raw else None
        if theoretical is True:
            theoretical_count += 1
        elif theoretical is False:
            practical_count += 1
        if level:
            level_counts[level] = level_counts.get(level, 0) + 1
        topics.append({
            'uri': str(t),
            'name': tname,
            'theoretical': theoretical,
            'main': main,
            'educationalLevel': level,
        })

    # Related skills via skills that require or teach these topics
    related_skills = []
    skill_seen = set()
    for t in _rdf_graph.objects(target, SCHEMA.teaches):
        # skills that require knowledge of t
        for s in _rdf_graph.subjects(EDUCOR.requiresKnowledge, t):
            for c in _rdf_graph.objects(s, COURSES.skillRequired):
                cid = str(c)
                if cid in skill_seen:
                    continue
                skill_seen.add(cid)
                related_skills.append({
                    'uri': cid,
                    'name': _first_literal(c, SCHEMA.name) or cid,
                    'educationalLevel': _first_literal(c, SCHEMA.educationalLevel)
                })
        # skills that teach t
        for s in _rdf_graph.subjects(SCHEMA.teaches, t):
            for c in _rdf_graph.objects(s, COURSES.skillRequired):
                cid = str(c)
                if cid in skill_seen:
                    continue
                skill_seen.add(cid)
                related_skills.append({
                    'uri': cid,
                    'name': _first_literal(c, SCHEMA.name) or cid,
                    'educationalLevel': _first_literal(c, SCHEMA.educationalLevel)
                })

    return {
        'uri': str(target),
        'name': name,
        'url': url,
        'providers': providers,
        'topics': topics,
        'summary': {
            'topic_count': len(topics),
            'theoretical_count': theoretical_count,
            'practical_count': practical_count,
            'levels': level_counts,
        },
        'skills': related_skills,
    }


def _text(o):
    if o is None:
        return ''
    return str(o)


def _string_similarity(a: str, b: str) -> float:
    a = (a or '').strip().lower()
    b = (b or '').strip().lower()
    if not a or not b:
        return 0.0
    # Boost exact or substring matches
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    return difflib.SequenceMatcher(None, a, b).ratio()


def _summarize_course_for_search(course_uri: URIRef):
    name = _first_literal(course_uri, SCHEMA.name)
    url = _first_literal(course_uri, SCHEMA.url)
    # Providers
    providers = []
    seen = set()
    for pred in (SCHEMA.provider, COURSES.responsibleEntity):
        for p in _rdf_graph.objects(course_uri, pred):
            pid = str(p)
            if pid in seen:
                continue
            seen.add(pid)
            ptype = 'Organization' if ((p, RDF.type, SCHEMA.EducationalOrganization) in _rdf_graph or (p, RDF.type, SCHEMA.CollegeOrUniversity) in _rdf_graph) else ('Person' if (p, RDF.type, SCHEMA.Person) in _rdf_graph else None)
            providers.append({
                'uri': pid,
                'name': _first_literal(p, SCHEMA.name) or pid,
                'type': ptype
            })

    # Topics and levels
    topics = []
    level_counts = {}
    theoretical_count = 0
    practical_count = 0
    for t in _rdf_graph.objects(course_uri, SCHEMA.teaches):
        tname = _first_literal(t, SCHEMA.name) or str(t)
        theoretical = _bool_value(t, COURSES.theoreticalTopic)
        if theoretical is True:
            theoretical_count += 1
        elif theoretical is False:
            practical_count += 1
        level_raw = _get_level_value(t)
        level_short = _short_level_label(level_raw) if level_raw else None
        if level_short:
            level_counts[level_short] = level_counts.get(level_short, 0) + 1
        main = _bool_value(t, COURSES.mainTopic)
        topics.append({'uri': str(t), 'name': tname, 'educationalLevel': level_short, 'theoretical': theoretical, 'main': main})

    # Skills related via skills
    skill_seen = set()
    skill_count = 0
    skills = []
    for t in _rdf_graph.objects(course_uri, SCHEMA.teaches):
        for s in _rdf_graph.subjects(EDUCOR.requiresKnowledge, t):
            for c in _rdf_graph.objects(s, COURSES.skillRequired):
                cid = str(c)
                if cid in skill_seen:
                    continue
                skill_seen.add(cid)
                skill_count += 1
                skills.append({'uri': cid, 'name': _first_literal(c, SCHEMA.name) or cid})
        for s in _rdf_graph.subjects(SCHEMA.teaches, t):
            for c in _rdf_graph.objects(s, COURSES.skillRequired):
                cid = str(c)
                if cid in skill_seen:
                    continue
                skill_seen.add(cid)
                skill_count += 1
                skills.append({'uri': cid, 'name': _first_literal(c, SCHEMA.name) or cid})

    # Build a small network graph (nodes/edges)
    nodes = []
    edges = []
    nodes.append({'id': str(course_uri), 'label': name or 'Course', 'group': 'Course'})
    # Providers
    for p in providers[:6]:
        nodes.append({'id': p['uri'], 'label': p['name'], 'group': p['type'] or 'Provider'})
        edges.append({'from': str(course_uri), 'to': p['uri'], 'label': 'provider'})
    # Topics
    limited_topics = topics[:12]
    included_topic_ids = {t['uri'] for t in limited_topics}
    for t in limited_topics:
        nodes.append({'id': t['uri'], 'label': t['name'], 'group': 'Topic'})
        edges.append({'from': str(course_uri), 'to': t['uri'], 'label': 'teaches'})

    # Skills (from skill_required logic above)
    for s in skills:
        if not any(n['id'] == s['uri'] for n in nodes):
            nodes.append({'id': s['uri'], 'label': s['name'], 'group': 'Skill'})
        edges.append({'from': str(course_uri), 'to': s['uri'], 'label': 'skill'})

    skills_shown = sum(1 for n in nodes if n.get('group') == 'Skill')

    summary = {
        'topic_count': len(topics),
        'theoretical_count': theoretical_count,
        'practical_count': practical_count,
        'levels': level_counts,
        'skills_count': skills_shown,
    }

    return {
        'uri': str(course_uri),
        'name': name,
        'url': url,
        'providers': providers,
        'topics': topics,
        'summary': summary,
        'graph': {'nodes': nodes, 'edges': edges},
        'skills': skills,
    }


def search_similar_courses_rdf(title_query: str, description_query: str = None, limit: int = 10):
    if not _rdf_graph:
        return []
    title_query = _text(title_query)
    description_query = _text(description_query)
    query = title_query if title_query else description_query
    if not query:
        return []

    scored = []
    for course in _rdf_graph.subjects(RDF.type, SCHEMA.Course):
        name = _first_literal(course, SCHEMA.name) or ''
        desc = _first_literal(course, SCHEMA.description) or ''
        # score based on provided fields, prefer title match
        score_title = _string_similarity(query, name) if title_query else 0.0
        score_desc = _string_similarity(query, desc) if description_query and not title_query else 0.0
        score = max(score_title, score_desc)
        if score >= 0.4:  # basic threshold to filter noise
            summary = _summarize_course_for_search(course)
            summary['match'] = {
                'title': name,
                'description_present': bool(desc),
                'score': round(score, 3)
            }
            scored.append(summary)

    scored.sort(key=lambda x: x['match']['score'], reverse=True)
    return scored[:limit]


def init_neo4j_connection():
    # Read credentials from .env file (preferred). Do not prefer OS env vars per request.
    uri = env_values.get('NEO4J_URI') or 'bolt://localhost:7687'
    username = env_values.get('NEO4J_USER') or 'neo4j'
    password = env_values.get('NEO4J_PASSWORD') or 'KG_edu_1'
    # Use a short connection timeout so health checks fail fast if DB is unavailable
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=3)
    except TypeError:
        # Older driver versions may not support connection_timeout; fall back gracefully
        driver = GraphDatabase.driver(uri, auth=(username, password))
    return driver


def find_empty_fields(facilitators, course_data, educational_resources, additional_resources):
    empty_fields = []
    for i, facilitator in enumerate(facilitators):
        if not facilitator.get('name'):
            empty_fields.append(f"Facilitator {i + 1} Name")
        if not facilitator.get('affiliation'):
            empty_fields.append(f"Facilitator {i + 1} Affiliation")
        if not facilitator.get('email'):
            empty_fields.append(f"Facilitator {i + 1} Email")
        if not facilitator.get('roles'):
            empty_fields.append(f"Facilitator {i + 1} Roles")

    if not course_data.get('title'):
        empty_fields.append("Course Title")
    if not course_data.get('description'):
        empty_fields.append("Course Description")
    if not course_data.get('notional_hours'):
        empty_fields.append("Notional Hours")
    if not course_data.get('topics'):
        empty_fields.append("Course Topics")
    if not course_data.get('learning_outcomes'):
        empty_fields.append("Course Learning Outcomes")
    if not course_data.get('targeted_skills'):
        empty_fields.append("Targeted Skills")

    if not course_data.get('educational_level'):
        empty_fields.append("Educational Level")
    if not course_data.get('language'):
        empty_fields.append("Language")
    if not course_data.get('entry_requirements'):
        empty_fields.append("Entry Requirements")
    if not course_data.get('required_software'):
        empty_fields.append("Required Software")

    for i, resource in enumerate(educational_resources):
        if not resource.get('title'):
            empty_fields.append(f"Educational Resource {i + 1} Title")
        if not resource.get('url'):
            empty_fields.append(f"Educational Resource {i + 1} URL")
        if not resource.get('type'):
            empty_fields.append(f"Educational Resource {i + 1} Type")

    for i, resource in enumerate(additional_resources):
        if not resource.get('type'):
            empty_fields.append(f"Additional Resource {i + 1} Type")
        if not resource.get('url'):
            empty_fields.append(f"Additional Resource {i + 1} URL")

    return empty_fields


def store_course_data(username, facilitators, course_data, educational_resources, additional_resources):
    driver = init_neo4j_connection()
    with driver.session() as session:
        # Create or merge Course node
        session.run(
            """
            MERGE (c:Course {title: $course_title})
            SET c.description = $course_description,
                c.notional_hours = $notional_hours,
                c.course_topics = $course_topics,
                c.learning_outcomes = $learning_outcomes,
                c.targeted_skills = $targeted_skills,
                c.educational_level = $educational_level,
                c.language = $language,
                c.entry_requirements = $entry_requirements,
                c.required_software = $required_software
            """,
            course_title=course_data.get('title'),
            course_description=course_data.get('description'),
            notional_hours=course_data.get('notional_hours'),
            course_topics=course_data.get('topics'),
            learning_outcomes=course_data.get('learning_outcomes'),
            targeted_skills=course_data.get('targeted_skills'),
            educational_level=course_data.get('educational_level'),
            language=course_data.get('language'),
            entry_requirements=course_data.get('entry_requirements'),
            required_software=course_data.get('required_software')
        )

        # Link course to user who created it
        if username:
            session.run(
                """
                MATCH (u:User {username: $username}), (c:Course {title: $course_title})
                MERGE (u)-[:CREATED]->(c)
                """,
                username=username,
                course_title=course_data.get('title')
            )

        # Create facilitators and link
        for facilitator in facilitators:
            session.run(
                """
                MERGE (f:Facilitator {name: $facilitator_name, affiliation: $affiliation, email: $email})
                SET f.roles = $roles
                MERGE (c:Course {title: $course_title})
                MERGE (f)-[:FACILITATES]->(c)
                """,
                facilitator_name=facilitator.get('name'),
                affiliation=facilitator.get('affiliation'),
                email=facilitator.get('email'),
                roles=facilitator.get('roles'),
                course_title=course_data.get('title')
            )

        # Educational resources
        for resource in educational_resources:
            session.run(
                """
                MERGE (e:EducationalResource {title: $resource_title, url: $resource_url})
                SET e.type = $resource_type
                MERGE (c:Course {title: $course_title})
                MERGE (c)-[:INCLUDES_RESOURCE]->(e)
                """,
                resource_title=resource.get('title'),
                resource_url=resource.get('url'),
                resource_type=resource.get('type'),
                course_title=course_data.get('title')
            )

        # Additional resources
        for resource in additional_resources:
            session.run(
                """
                MERGE (a:AdditionalResource {url: $additional_url})
                SET a.type = $additional_type
                MERGE (c:Course {title: $course_title})
                MERGE (c)-[:HAS_ADDITIONAL_RESOURCE]->(a)
                """,
                additional_url=resource.get('url'),
                additional_type=resource.get('type'),
                course_title=course_data.get('title')
            )

    driver.close()


def search_similar_courses(course_title):
    driver = init_neo4j_connection()
    with driver.session() as session:
        results = session.run(
            """
            MATCH (c:Course)
            WHERE c.title CONTAINS $course_title
            OPTIONAL MATCH (c)-[:FACILITATES]-(f:Facilitator)
            OPTIONAL MATCH (c)-[:INCLUDES_RESOURCE]->(e:EducationalResource)
            RETURN c.title AS course_title,
                   c.course_topics AS course_topics,
                   COLLECT(DISTINCT f.name) AS facilitators,
                   c.educational_level AS educational_level,
                   c.language AS language,
                   COLLECT(DISTINCT {title: e.title, url: e.url}) AS educational_resources
            """,
            course_title=course_title
        )

        out = []
        for record in results:
            out.append({
                'course_title': record['course_title'],
                'course_topics': record['course_topics'],
                'facilitators': record['facilitators'],
                'educational_level': record['educational_level'],
                'language': record['language'],
                'educational_resources': [ {'title': er['title'], 'url': er['url']} for er in record['educational_resources'] if er and er.get('title')]
            })
        driver.close()
        return out


def find_complementary_content(course_title, existing_resources_titles):
    driver = init_neo4j_connection()
    with driver.session() as session:
        results = session.run(
            """
            MATCH (c:Course)-[:INCLUDES_RESOURCE]->(e:EducationalResource)
            WHERE c.title CONTAINS $course_title AND NOT e.title IN $existing_resources_titles
            RETURN DISTINCT e.title AS title, e.url AS url
            """,
            course_title=course_title,
            existing_resources_titles=existing_resources_titles
        )

        out = [ {'Title': r['title'], 'URL': r['url']} for r in results ]
        driver.close()
        return out


def get_user(username):
    driver = init_neo4j_connection()
    with driver.session() as session:
        res = session.run("MATCH (u:User {username: $username}) RETURN u.username AS username, u.email AS email", username=username)
        rec = res.single()
    driver.close()
    return rec


def create_user(username, email, password_plain):
    password_hash = generate_password_hash(password_plain)
    driver = init_neo4j_connection()
    with driver.session() as session:
        session.run("MERGE (u:User {username: $username}) SET u.email = $email, u.password_hash = $password_hash",
                    username=username, email=email, password_hash=password_hash)
    driver.close()


def verify_user(username, password_plain):
    driver = init_neo4j_connection()
    with driver.session() as session:
        res = session.run("MATCH (u:User {username: $username}) RETURN u.password_hash AS pw", username=username)
        rec = res.single()
    driver.close()
    if rec and rec.get('pw'):
        return check_password_hash(rec['pw'], password_plain)
    return False


def login_required(func):
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('login'))
        return func(*args, **kwargs)

    return wrapper


@app.route('/')
def index():
    # Temporary landing page first
    return redirect(url_for('welcome'))


@app.route('/home')
def home():
    return render_template('index.html', user=session.get('username'))


@app.context_processor
def inject_current_year():
    return {'current_year': datetime.now(timezone.utc).year}


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/licensing')
def licensing():
    return render_template('licensing.html')


@app.route('/examples')
def examples():
    return render_template('examples.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        if not username or not password:
            flash('Username and password required', 'danger')
            return redirect(url_for('register'))
        create_user(username, email, password)
        flash('Registration complete. Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if verify_user(username, password):
            session['username'] = username
            flash('Logged in successfully', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid credentials', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    flash('Logged out', 'info')
    return redirect(url_for('index'))


@app.route('/add_course', methods=['GET', 'POST'])
@login_required
def add_course():
    if request.method == 'POST':
        # parse form data
        username = session.get('username')
        # Facilitators: we expect repeated fields facilitator_name_0..n
        facilitators = []
        i = 0
        while True:
            name = request.form.get(f'facilitator_name_{i}')
            if not name:
                break
            facilitators.append({
                'name': name,
                'affiliation': request.form.get(f'facilitator_affiliation_{i}'),
                'email': request.form.get(f'facilitator_email_{i}'),
                'roles': request.form.getlist(f'facilitator_roles_{i}')
            })
            i += 1

        course_data = {
            'title': request.form.get('course_title'),
            'description': request.form.get('course_description'),
            'notional_hours': request.form.get('notional_hours'),
            'topics': request.form.get('course_topics'),
            'learning_outcomes': request.form.get('learning_outcomes'),
            'targeted_skills': request.form.get('targeted_skills'),
            'educational_level': request.form.getlist('educational_level'),
            'language': request.form.getlist('language'),
            'entry_requirements': request.form.get('entry_requirements'),
            'required_software': request.form.get('required_software')
        }

        educational_resources = []
        j = 0
        while True:
            title = request.form.get(f'resource_title_{j}')
            if not title:
                break
            educational_resources.append({'title': title, 'url': request.form.get(f'resource_url_{j}'), 'type': request.form.getlist(f'resource_type_{j}')})
            j += 1

        additional_resources = []
        k = 0
        while True:
            url = request.form.get(f'additional_url_{k}')
            if not url:
                break
            additional_resources.append({'url': url, 'type': request.form.getlist(f'additional_type_{k}')})
            k += 1

        empty_fields = find_empty_fields(facilitators, course_data, educational_resources, additional_resources)
        if empty_fields:
            flash('Missing fields: ' + ', '.join(empty_fields), 'warning')
        # store regardless of empties for now but link to user
        store_course_data(username, facilitators, course_data, educational_resources, additional_resources)
        flash('Course stored', 'success')
        return redirect(url_for('add_course'))

    return render_template('add_course.html')


@app.route('/create_course', methods=['GET', 'POST'])
def create_course():
    results = []
    graph_data = None
    show_builder = False
    initial_title = ''

    if request.method == 'POST':
        form_name = request.form.get('form_name')
        if form_name == 'course_search':
            course_title = (request.form.get('course_title') or '').strip()
            if not course_title:
                flash('Please enter a course title to search.', 'warning')
            else:
                results = search_similar_courses_rdf(course_title, None)
                if not results:
                    flash('No similar courses found in the knowledge graph', 'info')
                else:
                    graph_data = results[0]['graph']
                    show_builder = True
                initial_title = course_title

    return render_template(
        'create_course.html',
        results=results,
        graph_data=graph_data,
        show_builder=show_builder,
        initial_title=initial_title
    )


# ISWC Test Use-Case: isolated Create page with minimal navbar
@app.route('/test', methods=['GET', 'POST'])
def create_course_test():
    results = None
    graph_data = None
    initial_title = ''
    show_builder = False

    if request.method == 'POST':
        form_name = request.form.get('form_name')
        if form_name == 'course_search':
            course_title = (request.form.get('course_title') or '').strip()
            if course_title:
                session['course_test_title'] = course_title
            else:
                flash('Please enter a course title to begin.', 'warning')
        return redirect(url_for('create_course_test'))

    stored_title = session.pop('course_test_title', None)
    if stored_title:
        initial_title = stored_title
        show_builder = True
        results = search_similar_courses_rdf(stored_title, None)
        if not results:
            results = []
            flash('No similar courses found in the knowledge graph', 'info')
        else:
            graph_data = results[0]['graph']

    return render_template(
        'create_course_test.html',
        results=results,
        graph_data=graph_data,
        show_builder=show_builder,
        initial_title=initial_title
    )


@app.route('/complete_course', methods=['GET', 'POST'])
def complete_course_route():
    results = []
    graph_data = None
    show_builder = False
    initial_title = ''

    if request.method == 'POST':
        form_name = request.form.get('form_name')
        if form_name == 'course_search':
            course_title = (request.form.get('course_title') or '').strip()
            if not course_title:
                flash('Please enter a course title to search.', 'warning')
            else:
                results = search_similar_courses_rdf(course_title, None)
                if not results:
                    flash('No similar courses found in the knowledge graph', 'info')
                else:
                    graph_data = results[0]['graph']
                    show_builder = True
                initial_title = course_title

    return render_template(
        'complete_course.html',
        results=results,
        graph_data=graph_data,
        show_builder=show_builder,
        initial_title=initial_title
    )


# New routes to expose RDF-backed course retrieval
@app.route('/welcome')
def welcome():
    return render_template('welcome.html')
@app.route('/courses', methods=['GET', 'POST'])
def courses():
    results = []
    graph_data = None
    show_builder = False
    initial_title = ''

    if request.method == 'POST':
        form_name = request.form.get('form_name')
        if form_name == 'course_search':
            course_title = (request.form.get('course_title') or '').strip()
            if not course_title:
                flash('Please enter a course title to search.', 'warning')
            else:
                results = search_similar_courses_rdf(course_title, None)
                if not results:
                    flash('No similar courses found in the knowledge graph', 'info')
                else:
                    graph_data = results[0]['graph']
                initial_title = course_title

    courses_list = list_courses_from_rdf()
    return render_template(
        'courses.html',
        courses=courses_list,
        results=results,
        graph_data=graph_data,
        show_builder=show_builder,
        initial_title=initial_title
    )


@app.route('/courses/<path:course_id>')
def course_detail(course_id):
    detail = course_detail_from_rdf(course_id)
    if not detail:
        # Also accept a query parameter ?id=... as a fallback for tricky encodings
        alt = request.args.get('id')
        if alt:
            detail = course_detail_from_rdf(alt)
    if not detail:
        flash('Course not found in knowledge graph', 'warning')
        return redirect(url_for('courses'))
    return render_template('course_detail.html', course=detail)


# Inline details fragment for Courses list (lazy-loaded)
@app.route('/courses/details')
def course_detail_fragment():
    course_id = request.args.get('id')
    if not course_id:
        return '<div class="alert alert-warning mb-0">Missing course identifier.</div>', 400

    detail = course_detail_from_rdf(course_id)
    if not detail:
        return '<div class="alert alert-warning mb-0">Course not found in the knowledge graph.</div>', 404

    return render_template(
        '_course_details_fragment.html',
        course=detail
    )


@app.route('/status')
def status():
    rdf_file_exists = os.path.exists(RDF_FILE)
    triple_count = len(_rdf_graph) if _rdf_graph else 0
    course_count = len(list_courses_from_rdf()) if _rdf_graph else 0

    neo4j = {
        'configured': bool(env_values.get('NEO4J_URI') or env_values.get('NEO4J_USER') or env_values.get('NEO4J_PASSWORD')),
        'connected': False
    }

    driver = None
    try:
        driver = init_neo4j_connection()
        if driver:
            with driver.session() as session:
                session.run("RETURN 1 AS ok").single()
            neo4j['connected'] = True
    except Exception as exc:
        neo4j['error'] = str(exc)
    finally:
        if driver:
            try:
                driver.close()
            except Exception:
                pass

    return jsonify({
        'status': 'ok' if rdf_file_exists else 'degraded',
        'rdf': {
            'file': RDF_FILE,
            'file_exists': rdf_file_exists,
            'triple_count': triple_count,
            'course_count': course_count
        },
        'neo4j': neo4j
    })


if __name__ == '__main__':
    # Allow PORT/FLASK_DEBUG overrides so the app can run locally or on hosting platforms.
    port = int(os.environ.get('PORT', 5000))
    debug_enabled = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=port, debug=debug_enabled)
