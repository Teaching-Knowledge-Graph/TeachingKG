import streamlit as st
from neo4j import GraphDatabase
import pandas as pd

# Neo4j connection setup
def init_neo4j_connection():
    uri = "bolt://localhost:7687"
    username = "neo4j"
    password = "KG_edu_1"
    driver = GraphDatabase.driver(uri, auth=(username, password))
    return driver

#Page configurations
st.set_page_config(layout="wide")


def store_course_data(facilitators, course_data, educational_resources, additional_resources):
    driver = init_neo4j_connection()
    with driver.session() as session:
        # Create or merge Course node with updated properties
        session.run("""
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
            """, course_title=course_data['title'],
            course_description=course_data['description'],
            notional_hours=course_data['notional_hours'],
            course_topics=course_data['topics'],
            learning_outcomes=course_data['learning_outcomes'],
            targeted_skills=course_data['targeted_skills'],
            educational_level=course_data['educational_level'],
            language=course_data['language'],
            entry_requirements=course_data['entry_requirements'],
            required_software=course_data['required_software'])

        # Create or merge Facilitator nodes and connect them to the Course
        for facilitator in facilitators:
            session.run("""
                MERGE (f:Facilitator {name: $facilitator_name, affiliation: $affiliation, email: $email})
                SET f.roles = $roles
                MERGE (c:Course {title: $course_title})
                MERGE (f)-[:FACILITATES]->(c)
                """, facilitator_name=facilitator['name'], affiliation=facilitator['affiliation'],
                email=facilitator['email'], roles=facilitator['roles'],
                course_title=course_data['title'])

        # Create Educational Resource nodes and connect each to the Course
        for resource in educational_resources:
            session.run("""
                MERGE (e:EducationalResource {title: $resource_title, url: $resource_url})
                SET e.type = $resource_type
                MERGE (c:Course {title: $course_title})
                MERGE (c)-[:INCLUDES_RESOURCE]->(e)
                """, resource_title=resource['title'],
                resource_url=resource['url'],
                resource_type=resource['type'],
                course_title=course_data['title'])

        # Create Additional Resource nodes and connect each to the Course
        for i, resource in enumerate(additional_resources):
            session.run("""
                MERGE (a:AdditionalResource {url: $additional_url})
                SET a.type = $additional_type
                MERGE (c:Course {title: $course_title})
                MERGE (c)-[:HAS_ADDITIONAL_RESOURCE]->(a)
                """, additional_url=resource['url'],
                additional_type=resource['type'],
                course_title=course_data['title'])

    driver.close()


def facilitator_form(index):
    st.subheader(f"Facilitator {index + 1}")
    col1, col2 = st.columns(2)
    with col1:
        facilitator_name = st.text_input(f"Facilitator {index + 1} Name")
        facilitator_email = st.text_input(f"Facilitator {index + 1} Email")
    with col2:
        affiliation = st.text_input(f"Facilitator {index + 1} Affiliation")
        roles = st.multiselect(f"Facilitator {index + 1} Roles", ["Tutor", "Teaching Assistant", "Contact Person"])
    return {"name": facilitator_name, "affiliation": affiliation, "email": facilitator_email, "roles": roles}


def find_empty_fields(facilitators, course_data, educational_resources, additional_resources):
    empty_fields = []

    # Check each facilitator field
    for i, facilitator in enumerate(facilitators):
        if not facilitator['name']:
            empty_fields.append(f"Facilitator {i + 1} Name")
        if not facilitator['affiliation']:
            empty_fields.append(f"Facilitator {i + 1} Affiliation")
        if not facilitator['email']:
            empty_fields.append(f"Facilitator {i + 1} Email")
        if not facilitator['roles']:
            empty_fields.append(f"Facilitator {i + 1} Roles")

    # Check course data fields
    if not course_data['title']:
        empty_fields.append("Course Title")
    if not course_data['description']:
        empty_fields.append("Course Description")
    if not course_data['notional_hours']:
        empty_fields.append("Notional Hours")
    if not course_data['topics']:
        empty_fields.append("Course Topics")
    if not course_data['learning_outcomes']:
        empty_fields.append("Course Learning Outcomes")
    if not course_data['targeted_skills']:
        empty_fields.append("Targeted Skills")

    # Check target audience fields
    if not course_data['educational_level']:
        empty_fields.append("Educational Level")
    if not course_data['language']:
        empty_fields.append("Language")
    if not course_data['entry_requirements']:
        empty_fields.append("Entry Requirements")
    if not course_data['required_software']:
        empty_fields.append("Required Software")

    # Check each educational resource field
    for i, resource in enumerate(educational_resources):
        if not resource['title']:
            empty_fields.append(f"Educational Resource {i + 1} Title")
        if not resource['url']:
            empty_fields.append(f"Educational Resource {i + 1} URL")
        if not resource['type']:
            empty_fields.append(f"Educational Resource {i + 1} Type")

    # Check each additional resource field
    for i, resource in enumerate(additional_resources):
        if not resource['type']:
            empty_fields.append(f"Additional Resource {i + 1} Type")
        if not resource['url']:
            empty_fields.append(f"Additional Resource {i + 1} URL")

    return empty_fields


def search_similar_courses(course_title):
    driver = init_neo4j_connection()
    with driver.session() as session:
        results = session.run("""
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
                    """, course_title=course_title)

        return [{"course_title": record["course_title"],
                 "course_topics": record["course_topics"],
                 "facilitators": record["facilitators"],
                 "educational_level": record["educational_level"],
                 "language": record["language"],
                 "educational_resources": [{"title": er["title"], "url": er["url"]} for er in
                                           record["educational_resources"]]}
                for record in results]
    driver.close()


# search for complementary educational resources
def find_complementary_content(course_title, existing_resources_titles):
    driver = init_neo4j_connection()
    with driver.session() as session:
        results = session.run("""
            MATCH (c:Course)-[:INCLUDES_RESOURCE]->(e:EducationalResource)
            WHERE c.title CONTAINS $course_title AND NOT e.title IN $existing_resources_titles
            RETURN DISTINCT e.title AS title, e.url AS url
            """, course_title=course_title, existing_resources_titles=existing_resources_titles)

        return [{"Title": record["title"], "URL": record["url"]} for record in results]
    driver.close()


tabs = st.tabs(["Home", "About the Interface", "Licensing Information", "Course Examples"])


# App layout
def main():
    # Tab 1: Home tab
    with tabs[0]:
        st.sidebar.title("What would you like to do?")
        pages = ["Add New Course", "Create Your Course", "Complete Your Existing Course"]
        selection = st.sidebar.radio("Go to", pages)

        if selection == "Add New Course":
            add_new_course()
        elif selection == "Create Your Course":
            create_your_course()
        elif selection == "Complete Your Existing Course":
            complete_your_course()

    # Tab 2: About the Interface
    with tabs[1]:
        st.header("About the Interface")
        st.write("""
            This interface is designed to help users create and manage course data, 
            search for similar courses, and find complementary educational resources. 
            Each section offers tools for navigating the course data structure and adding new content with ease.
            """)

    # Tab 3: Licensing Information
    with tabs[2]:
        st.header("Licensing Information")
        st.write("""
            The interface and the associated code are licensed under the [MIT License](https://opensource.org/licenses/MIT). 
            The data provided within the interface may be subject to additional licensing terms.
            Please consult the documentation for further details.
            """)

    # Tab 4: Course Examples
    with tabs[3]:
        st.header("Course Examples")
        st.write("""
            Here are some example courses to help you understand how to structure and add course data:
            - **Data Science 101**: An introductory course to data science covering basic statistics, data manipulation, and machine learning.
            - **Web Development Bootcamp**: A complete course on front-end and back-end web development, using HTML, CSS, JavaScript, and Python.
            - **Advanced Machine Learning**: Focuses on deep learning models, neural networks, and advanced ML techniques.
            """)


# Page 1: Add New Course
def add_new_course():
    st.title("Add New Course")
    st.divider()

    # Facilitator info
    st.header("Facilitator Info")
    facilitators = []
    num_facilitators = st.number_input("Number of Facilitators", min_value=1, step=1, value=1)

    for i in range(num_facilitators):
        facilitator_data = facilitator_form(i)
        facilitators.append(facilitator_data)

    st.divider()

    # Course Data
    st.header("Course Data")
    col1, col2 = st.columns(2)
    with col1:
        course_title = st.text_input("Course Title")
        course_description = st.text_area("Course Description")
        course_topics = st.text_area("Course Topics")
    with col2:
        notional_hours = st.text_input("Notional Hours")
        learning_outcomes = st.text_area("Course Learning Outcomes")
        targeted_skills = st.text_area("Targeted Skills")

    course_data = {
        "title": course_title,
        "description": course_description,
        "notional_hours": notional_hours,
        "topics": course_topics,
        "learning_outcomes": learning_outcomes,
        "targeted_skills": targeted_skills
    }

    st.divider()

    # Target Audience
    st.header("Target Audience")
    col1, col2 = st.columns(2)
    with col1:
        educational_level = st.multiselect("Educational Level", ["Undergraduate", "Graduate", "Postgraduate"])
        entry_requirements = st.text_area("Entry Requirements")
    with col2:
        language = st.multiselect("Language", ["English", "Spanish", "German", "French", "Other"])
        required_software = st.text_area("Required Software")

    course_data.update({
        "educational_level": educational_level,
        "language": language,
        "entry_requirements": entry_requirements,
        "required_software": required_software
    })

    st.divider()

    # Course Educational Resources Section
    st.header("Course Educational Resources")
    num_resources = st.number_input("Number of Educational Resources", min_value=1, step=1)

    educational_resources = []
    for i in range(1, num_resources + 1):
        st.subheader(f"Educational Resource {i} Data")
        col1, col2 = st.columns(2)
        with col1:
            resource_title = st.text_input(f"Educational Resource {i} Title")
            resource_url = st.text_input(f"Educational Resource {i} URL")
        with col2:
            resource_type = st.multiselect(
                f"Educational Resource {i} Type",
                ["Learning Content", "Assessment", "Dataset"]
            )
        educational_resources.append({
            "title": resource_title,
            "url": resource_url,
            "type": resource_type
        })

    # Additional Resources Section
    st.subheader("Additional Resources")
    num_additional_resources = st.number_input("Number of Additional Resources", min_value=1, step=1)

    additional_resources = []
    for i in range(1, num_additional_resources + 1):
        st.subheader(f"Additional Resource {i}")
        col1, col2 = st.columns(2)
        with col1:
            resource_type = st.multiselect(
                f"Type (Additional Resource {i})",
                ["External Link", "Dataset", "Social Media", "OER"],
                key=f"additional_resource_type_{i}"
            )
        with col2:
            resource_url = st.text_input(f"URL (Additional Resource {i})", key=f"additional_resource_url_{i}")

        additional_resources.append({
            "type": resource_type,
            "url": resource_url
        })


    if "confirm_submission" not in st.session_state:
        st.session_state.confirm_submission = False

    # Submit button
    if st.button("Submit"):
        empty_fields = find_empty_fields(facilitators, course_data, educational_resources, additional_resources)

        if empty_fields:
            # Show a warning with a list of all empty fields
            st.warning("The following fields are empty: \n- " + "\n- ".join(empty_fields))
            st.session_state.confirm_submission = True

        else:
            # If no fields are empty, directly store the data
            store_course_data(facilitators, course_data, educational_resources, additional_resources)
            st.success("Course data submitted successfully!")
            st.session_state.confirm_submission = False

    # Check if the user has confirmed submission with missing fields
    if st.session_state.confirm_submission:
        if st.button("Confirm Submission"):
            # Store the course data even with empty fields confirmed
            store_course_data(facilitators, course_data, educational_resources, additional_resources)
            st.success("Course data submitted successfully!")
            st.session_state.confirm_submission = False


# Page 2: Create Your Course
def create_your_course():
    st.title("Create Your Course")
    st.divider()
    # Course Data Section
    st.header("Course Data")
    col1, col2 = st.columns(2)
    with col1:
        course_title = st.text_input("Course Title")
        course_description = st.text_area("Course Description")

        course_topics = st.text_area("Course Topics")
    with col2:
        notional_hours = st.text_input("Notional Hours")
        learning_outcomes = st.text_area("Course Learning Outcomes")
        targeted_skills = st.text_area("Targeted Skills")

    course_data = {
        "title": course_title,
        "description": course_description,
        "notional_hours": notional_hours,
        "topics": course_topics,
        "learning_outcomes": learning_outcomes,
        "targeted_skills": targeted_skills
    }

    st.divider()

    # Target Audience Section
    st.header("Target Audience")
    col1, col2 = st.columns(2)
    with col1:
        educational_level = st.multiselect("Educational Level", ["Undergraduate", "Graduate", "Postgraduate"])
        entry_requirements = st.text_area("Entry Requirements")
    with col2:
        language = st.multiselect("Language", ["English", "Spanish", "German", "French", "Other"])
        required_software = st.text_area("Required Software")

    course_data.update({
        "educational_level": educational_level,
        "language": language,
        "entry_requirements": entry_requirements,
        "required_software": required_software
    })

    if st.button("Search Similar Courses"):
        # Check for empty fields and display warning if any
        empty_fields = find_empty_fields([], course_data, [], [])
        if empty_fields:
            st.info("You might consider adding some important missing information. The empty fields are: \n- " + "\n- ".join(empty_fields))

        # Proceed with the search regardless of empty fields
        similar_courses = search_similar_courses(course_title)
        if similar_courses:
            st.subheader("Similar Courses Found")

            # Prepare data for DataFrame
            course_table = []
            for course in similar_courses:
                resources = "; ".join([f"{er['title']} ({er['url']})" for er in course["educational_resources"]])
                course_table.append({
                    "Course Title": course["course_title"],
                    "Course Topics": course["course_topics"],
                    "Facilitators": ", ".join(course["facilitators"]),
                    "Educational Level": course["educational_level"],
                    "Language": course["language"],
                    "Educational Resources": resources
                })

            # Convert to DataFrame and display with full width
            course_df = pd.DataFrame(course_table)
            st.dataframe(course_df, use_container_width=True)
        else:
            st.warning("No similar courses found.")


# Page 3: Complete Your Existing Course
def complete_your_course():
    st.title("Complete Your Existing Course")
    st.divider()
    # Course Data Section
    st.header("Course Data")
    col1, col2 = st.columns(2)
    with col1:
        course_title = st.text_input("Course Title")
        course_description = st.text_area("Course Description")
        course_topics = st.text_area("Course Topics")
    with col2:
        notional_hours = st.text_input("Notional Hours")
        learning_outcomes = st.text_area("Course Learning Outcomes")
        targeted_skills = st.text_area("Targeted Skills")

    course_data = {
        "title": course_title,
        "description": course_description,
        "notional_hours": notional_hours,
        "topics": course_topics,
        "learning_outcomes": learning_outcomes,
        "targeted_skills": targeted_skills
    }

    st.divider()

    # Target Audience Section
    st.header("Target Audience")
    col1, col2 = st.columns(2)
    with col1:
        educational_level = st.multiselect("Educational Level", ["Undergraduate", "Graduate", "Postgraduate"])
        entry_requirements = st.text_area("Entry Requirements")
    with col2:
        language = st.multiselect("Language", ["English", "Spanish", "German", "French", "Other"])
        required_software = st.text_area("Required Software")

    course_data.update({
        "educational_level": educational_level,
        "language": language,
        "entry_requirements": entry_requirements,
        "required_software": required_software
    })

    st.divider()

    # Course Educational Resources Section
    st.header("Course Educational Resources")
    num_resources = st.number_input("Number of Educational Resources", min_value=1, step=1)

    educational_resources = []
    for i in range(1, num_resources + 1):
        st.subheader(f"Educational Resource {i}")
        col1, col2 = st.columns(2)
        with col1:
            resource_title = st.text_input(f"Title (Resource {i})")
            resource_url = st.text_input(f"URL (Resource {i})")
        with col2:
            resource_type = st.multiselect(f"Type (Resource {i})", ["Learning Content", "Assessment", "Dataset"])

        educational_resources.append({
            "title": resource_title,
            "url": resource_url,
            "type": resource_type
        })

    # Extract titles of existing educational resources for exclusion in the search
    existing_resources_titles = [resource["title"] for resource in educational_resources]

    # Find Complementary Content button
    if st.button("Find Complementary Content"):
        complementary_content = find_complementary_content(course_title, existing_resources_titles)
        if complementary_content:
            # Display complementary resources in a DataFrame table
            st.subheader("Complementary Educational Resources Found")
            content_df = pd.DataFrame(complementary_content)
            st.dataframe(content_df, use_container_width=True) 
        else:
            st.info("No complementary educational resources found.")


if __name__ == '__main__':
    main()
