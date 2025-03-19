import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
import pandas as pd
import google.generativeai as genai
import spacy
import re
from dateparser import parse
import en_core_web_sm
import urllib.parse

# 📌 Google Calendar API Setup
SERVICE_ACCOUNT_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]  # Full access to manage calendars
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build("calendar", "v3", credentials=credentials)

# 📌 Configure Gemini API
genai.configure(api_key="")
model = genai.GenerativeModel('gemini-1.5-flash')

# Load spaCy model
try:
    nlp = en_core_web_sm.load()
except OSError:
    st.info("Installing required language model...")
    import subprocess
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
    nlp = en_core_web_sm.load()

def extract_emails(text):
    """Extract email addresses from text using regex and NLP"""
    # Basic email regex pattern
    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
    
    # Find all matches using regex
    potential_emails = re.findall(email_pattern, text.lower())
    
    # Use NLP to improve accuracy
    doc = nlp(text)
    
    # Look for email-like patterns in named entities
    for ent in doc.ents:
        if ent.label_ == "EMAIL" or "@" in ent.text:
            email = ent.text.strip().lower()
            if re.match(email_pattern, email):
                potential_emails.append(email)
    
    return list(set(potential_emails))  # Remove duplicates

def extract_date(text):
    """Extract date from text using dateparser's natural language processing"""
    # Clean up input
    text = text.lower().strip()
    
    # Let dateparser handle all natural language date parsing
    parsed_date = parse(text, settings={'PREFER_DATES_FROM': 'future'})
    if parsed_date:
        return parsed_date.date()
    
    return None

def extract_working_hours(text):
    """Extract working hours from natural language text"""
    text = text.lower().strip()
    
    # Try to find time patterns like "9 am to 5 pm" or "9:00 to 17:00"
    time_patterns = [
        r'(\d{1,2})(?::?\d{2})?\s*(?:am|pm)?\s*(?:to|-)\s*(\d{1,2})(?::?\d{2})?\s*(?:am|pm)',
        r'(\d{1,2})(?::?\d{2})?\s*(?:to|-)\s*(\d{1,2})(?::?\d{2})?'
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            start_time, end_time = match.groups()
            
            # Convert to 24-hour format
            start_hour = int(start_time)
            end_hour = int(end_time)
            
            # Handle AM/PM if present
            if 'pm' in text:
                if end_hour != 12:
                    end_hour += 12
                if 'am' not in text and start_hour != 12:
                    start_hour += 12
            
            # Basic validation
            if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
                return start_hour, end_hour
            
    return None

def filter_slots_by_working_hours(slots, start_hour, end_hour):
    """Filter slots to only include those within working hours"""
    filtered_slots = []
    for slot in slots:
        slot_start_hour = slot['start'].hour
        slot_end_hour = slot['end'].hour
        
        # Only include slots that fall within working hours
        if start_hour <= slot_start_hour and slot_end_hour <= end_hour:
            filtered_slots.append(slot)
    
    return filtered_slots

# 📌 Streamlit Chat UI
st.set_page_config(page_title="AI Interview Scheduler", layout="wide")
st.title("SchedulAI 🤖 ",)

# Initialize session state
if "step" not in st.session_state:
    st.session_state.step = "initial"
    st.session_state.messages = []
    st.session_state.user_email = None
    st.session_state.candidate_email = None
    st.session_state.interview_date = None
    st.session_state.interview_duration = None
    st.session_state.free_slots = []
    st.session_state.selected_slot = None
    st.session_state.working_hours = None  # New state variable for working hours
    st.session_state.confirmation_state = None
    st.session_state.modification_type = None
    st.session_state.previous_step = None

# Function to get AI response
def get_ai_response(prompt):
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        st.error(f"Error getting AI response: {str(e)}")
        return None

INITIAL_PROMPT = """
Hi there, My name is :orange[**SchedulAI**].

I am an AI Interview Scheduler assistant. I'll help you schedule interviews efficiently.

**You can access [User Manual](https://github.com/Satwik-uppada/AI-Powered-Interview-Schedular/blob/main/USERMANUAL.md) here.**

Please provide the following information:
1. Your email (recruiter)
2. Your preferred working hours
3. Candidate's email
4. Interview duration (e.g., '1 hour', '30 minutes')
5. Preferred interview date

I'll help you find common free time slots and schedule the interview.

Please provide the **Recruiter email** to get started.
"""

def validate_date(date_str):
    """Validate date string using natural language processing"""
    try:
        # First try to parse as natural language date
        parsed_date = parse(date_str, settings={'PREFER_DATES_FROM': 'future'})
        if parsed_date:
            date = parsed_date.date()
        else:
            # Fallback to strict format if natural language parsing fails
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        today = datetime.now().date()
        if date < today:
            return False, "The interview date you provided is in the past. Please provide a valid future date or today's date."
        return True, date
    except (ValueError, TypeError):
        return False, "I couldn't understand the date format. Please provide a date like 'next Monday', 'March 25th', or 'YYYY-MM-DD'."

def extract_duration(text):
    """Extract duration in minutes from text"""
    text = text.lower().strip()
    total_minutes = 0
    
    # Handle combined hour and minute patterns
    hour_match = re.search(r'(\d+)\s*(?:hour|hr)s?', text)
    minute_match = re.search(r'(\d+)\s*(?:minute|min)s?', text)
    
    if hour_match:
        total_minutes += int(hour_match.group(1)) * 60
    if minute_match:
        total_minutes += int(minute_match.group(1))
    
    if total_minutes > 0:
        return total_minutes
        
    # Fallback: Try to extract just numbers (assume minutes)
    match = re.search(r'(\d+)', text)
    if match:
        num = int(match.group(1))
        # If number is less than 8, assume hours
        if num < 8:
            return num * 60
        return num
    
    return None

def split_slot_by_duration(slot_start, slot_end, duration_minutes):
    """Split a time slot into smaller slots of given duration using 5-minute increments"""
    slots = []
    # Use 5-minute increments for more granular slot generation
    increment_minutes = 5
    
    # Calculate total duration of the free block in minutes
    total_duration = int((slot_end - slot_start).total_seconds() / 60)
    
    # Calculate how many complete duration_minutes slots could fit with 5-min increments
    possible_start_times = range(0, total_duration - duration_minutes + 1, increment_minutes)
    
    # Generate all possible slots
    for offset in possible_start_times:
        start_time = slot_start + timedelta(minutes=offset)
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        # Only add slot if it fits completely within the free time block
        if end_time <= slot_end:
            slots.append({
                "start": start_time,
                "end": end_time
            })
    
    return slots

def convert_utc_to_ist(utc_dt):
    """Convert UTC datetime to IST timezone"""
    if not isinstance(utc_dt, datetime):
        print(f"❌ convert_utc_to_ist() received non-datetime object: {utc_dt}")
        return utc_dt
    return utc_dt.astimezone(timezone(timedelta(hours=5, minutes=30)))

def get_free_slots(users, date, duration_minutes, working_hours=None):
    # Set the day boundaries in IST directly
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    
    # If working hours are specified, use them instead of full day
    if working_hours:
        start_hour, end_hour = working_hours
        start_time_ist = datetime.combine(date, datetime.min.time()).replace(hour=start_hour, tzinfo=ist_tz)
        end_time_ist = datetime.combine(date, datetime.min.time()).replace(hour=end_hour, tzinfo=ist_tz)
    else:
        start_time_ist = datetime.combine(date, datetime.min.time()).replace(tzinfo=ist_tz)
        end_time_ist = datetime.combine(date, datetime.max.time()).replace(tzinfo=ist_tz)
    
    # Convert IST boundaries to UTC for API call
    start_time_utc = start_time_ist.astimezone(timezone.utc)
    end_time_utc = end_time_ist.astimezone(timezone.utc)
    
    request_body = {
        "timeMin": start_time_utc.isoformat(),
        "timeMax": end_time_utc.isoformat(),
        "items": [{"id": email} for email in users]
    }
    
    try:
        response = service.freebusy().query(body=request_body).execute()
        calendars = response.get("calendars", {})
        
        # Process busy slots for each user separately
        user_busy_slots = {}
        for user, calendar in calendars.items():
            if "errors" in calendar:
                st.warning(f"⚠️ Cannot access calendar for {user}")
                continue
            
            # Convert busy slots to IST
            busy_slots_ist = []
            for slot in calendar.get("busy", []):
                start_time = datetime.fromisoformat(slot["start"].replace("Z", "+00:00"))
                end_time = datetime.fromisoformat(slot["end"].replace("Z", "+00:00"))
                # Convert to IST before adding to busy slots
                start_ist = convert_utc_to_ist(start_time)
                end_ist = convert_utc_to_ist(end_time)
                
                # Only include slots that overlap with our target date in IST
                if (start_ist.date() == date or end_ist.date() == date):
                    busy_slots_ist.append({
                        "start": start_ist,
                        "end": end_ist
                    })
            
            # Sort and merge any overlapping busy slots
            busy_slots_ist.sort(key=lambda x: x["start"])
            merged_busy_slots = []
            for slot in busy_slots_ist:
                if not merged_busy_slots or merged_busy_slots[-1]["end"] < slot["start"]:
                    merged_busy_slots.append(slot)
                else:
                    merged_busy_slots[-1]["end"] = max(merged_busy_slots[-1]["end"], slot["end"])
            
            user_busy_slots[user] = merged_busy_slots
        
        # Find free slots for each user using IST times
        user_free_slots = {}
        for user, busy_slots in user_busy_slots.items():
            user_free_slots[user] = get_user_free_slots(busy_slots, start_time_ist, end_time_ist)
        
        # Find common free slots between users
        common_free_slots = []
        if len(user_free_slots) == 2:
            user1, user2 = list(user_free_slots.keys())
            for slot1 in user_free_slots[user1]:
                for slot2 in user_free_slots[user2]:
                    common_start = max(slot1["start"], slot2["start"])
                    common_end = min(slot1["end"], slot2["end"])
                    if common_start < common_end:
                        # Ensure slots stay within the target date
                        if common_start.date() == date or common_end.date() == date:
                            common_free_slots.append({
                                "start": common_start,
                                "end": common_end
                            })
        
        # Sort common free slots by start time
        common_free_slots.sort(key=lambda x: x["start"])
        
        # Split common free slots into interview duration slots
        split_slots = []
        for slot in common_free_slots:
            # Only split slots within our target date
            if slot["start"].date() == date or slot["end"].date() == date:
                duration_slots = split_slot_by_duration(slot["start"], slot["end"], duration_minutes)
                split_slots.extend(duration_slots)
        
        # Filter slots by working hours if specified
        if working_hours:
            split_slots = filter_slots_by_working_hours(split_slots, start_hour, end_hour)
        
        return {
            "common_free_slots": common_free_slots,
            "split_slots": split_slots
        }
        
    except Exception as e:
        st.error(f"Error fetching calendar data: {str(e)}")
        return {"common_free_slots": [], "split_slots": []}

def get_user_free_slots(busy_slots, start_of_day, end_of_day):
    """Calculate free slots by subtracting busy slots from the full day window."""
    free_slots = []
    current_start = start_of_day

    for slot in sorted(busy_slots, key=lambda x: x["start"]):
        if current_start < slot["start"]:  
            free_slots.append({"start": current_start, "end": slot["start"]})
        current_start = max(current_start, slot["end"])  

    if current_start < end_of_day:  
        free_slots.append({"start": current_start, "end": end_of_day})

    return free_slots

def process_user_input(user_input):
    if st.session_state.step == "initial":
        with st.spinner("Validating email...", show_time=True):
            # Extract emails from input
            emails = extract_emails(user_input)
            if emails:
                st.session_state.user_email = emails[0]
                st.session_state.step = "working_hours"
                return "✅ Found your email. To help schedule interviews efficiently, please specify your preferred working hours (e.g., '9 AM to 5 PM' or '10:00 to 18:00')."
            return "I couldn't find a valid email address in your input. Please provide your email address."

    elif st.session_state.step == "working_hours":
        with st.spinner("Processing working hours..."):
            working_hours = extract_working_hours(user_input)
            if working_hours:
                st.session_state.working_hours = working_hours
                st.session_state.step = "candidate_email"
                return f"✅ Working hours set to {working_hours[0]:02d}:00 to {working_hours[1]:02d}:00 IST.\n\nNow, please provide the **candidate's** email."
            return "I couldn't understand the working hours format. Please specify like '9 AM to 5 PM' or '10:00 to 18:00'."

    elif st.session_state.step == "candidate_email":
        with st.spinner("Validating candidate email..."):
            # Extract emails, excluding the recruiter's email
            emails = [email for email in extract_emails(user_input) 
                     if email != st.session_state.user_email]
            if emails:
                st.session_state.candidate_email = emails[0]
                st.session_state.step = "interview_duration"
                return f"✅ Found candidate's email: {emails[0]}\n\nHow long should the interview be? (e.g., '1 hour', '30 minutes', '45 min', '1 hr 30 min')"
            return "I couldn't find a valid email address for the candidate. Please provide the candidate's email."

    elif st.session_state.step == "interview_duration":
        with st.spinner("Processing duration...", show_time=True):
            duration_minutes = extract_duration(user_input)
            if duration_minutes:
                st.session_state.interview_duration = duration_minutes
                st.session_state.step = "interview_date"
                return f"✅ Interview duration set to {duration_minutes} minutes.\n\nWhat's your preferred interview date? You can use natural language like 'next Monday' or 'March 25th'."
            return "I couldn't understand the duration. Please specify like '1 hour', '30 minutes', or '45 min', '1 hr 30 min'."

    elif st.session_state.step == "interview_date":
        # Extract date using NLP
        with st.spinner("Analyzing date...", show_time=True):
            extracted_date = extract_date(user_input)
            if extracted_date:
                if extracted_date < datetime.now().date():
                    return "The date you provided is in the past. Please provide a future date."
                
                st.session_state.interview_date = extracted_date
                
                with st.spinner("Fetching calendar availability..."):
                    users = [st.session_state.user_email, st.session_state.candidate_email]
                    slots_result = get_free_slots(users, extracted_date, st.session_state.interview_duration, 
                                               working_hours=st.session_state.working_hours)
                
                if slots_result["split_slots"]:
                    st.session_state.free_slots = slots_result["split_slots"]  # Store split slots for selection
                    
                    # Format common free time blocks with full date-time for clarity
                    common_slots_text = "\n".join([
                        f"📆 {slot['start'].strftime('%H:%M')} to {slot['end'].strftime('%H:%M')}" 
                        for slot in slots_result["common_free_slots"]
                    ])
                    
                    # Format split slots with numbers for selection, showing only time for clarity
                    split_slots_text = "\n".join([
                        f"{i+1}. {slot['start'].strftime('%H:%M')} to {slot['end'].strftime('%H:%M')}" 
                        for i, slot in enumerate(slots_result["split_slots"])
                    ])
                    
                    st.session_state.step = "select_slot"
                    return f"""✅ Available time slots for {extracted_date.strftime('%A, %B %d, %Y')}:

Common Free Time Blocks (IST):
{common_slots_text}

Available {st.session_state.interview_duration}-minute Interview Slots (Select from these numbered slots):
{split_slots_text}

Please select an interview slot by entering its number (1-{len(slots_result["split_slots"])})."""
                return f"**No common free slots** found for {extracted_date.strftime('%A, %B %d')} with duration of **{st.session_state.interview_duration} minutes**. Please try **another date**."
            return "I couldn't understand the date. Please provide a date like **'next Monday' or 'March 25th'**."

    elif st.session_state.step == "select_slot":
        try:
            slot_index = int(user_input) - 1
            if 0 <= slot_index < len(st.session_state.free_slots):
                selected_slot = st.session_state.free_slots[slot_index]
                st.session_state.selected_slot = selected_slot
                st.session_state.step = "confirm_scheduling"
                
                # Generate confirmation message
                confirmation = f"""
🎯 Please confirm the interview details:

📧 Recruiter: {st.session_state.user_email}
📧 Candidate: {st.session_state.candidate_email}
📅 Date: {st.session_state.interview_date.strftime('%A, %B %d, %Y')}
⏰ Time: {selected_slot['start'].strftime('%H:%M')} to {selected_slot['end'].strftime('%H:%M')}
⏱️ Duration: {st.session_state.interview_duration} minutes

Would you like to proceed with scheduling this interview?
"""
                return confirmation
            return "**Invalid slot number**. Please select a **valid number** from the list."
        except ValueError:
            return "Please enter a **valid number** to select a time slot."

    elif st.session_state.step == "modification_choice":
        if user_input.lower() == "recruiter email":
            st.session_state.previous_step = st.session_state.step
            st.session_state.step = "initial"
            st.session_state.user_email = None
            return "Please provide the new recruiter email."
        elif user_input.lower() == "candidate email":
            st.session_state.previous_step = st.session_state.step
            st.session_state.step = "candidate_email"
            st.session_state.candidate_email = None
            return "Please provide the new candidate email."
        else:
            st.session_state.step = "interview_date"
            return "What's your preferred interview date? You can use natural language like **'next Monday' or 'March 25th'**."

    return "***I don't understand. Please follow the instructions.***"

def generate_email_template(event_details, sharing_link):
    """Generate a professional email template using Gemini AI"""
    try:
        prompt = f"""
        Generate a professional and friendly email template for an interview invitation with these details:
        - Date: {event_details['date']}
        - Time: {event_details['time']}
        - Duration: {event_details['duration']} minutes
        - Recruiter: {event_details['recruiter']}
        - Calendar Link: {sharing_link}
        
        The email should:
        1. Be professional but warm
        2. Include all scheduling details clearly
        3. Have clear instructions to click the calendar link to accept the interview invite
        4. Request the candidate to confirm receipt of the email
        5. Mention the importance of being on time
        6. Be concise but complete
        7. Include the calendar link with instructions on how to add it to their calendar
        8. Dont use any placeholder any where. Use the actual details provided above. start with dear candidate. No need to specify name.
        9. Don't include subject line.
        10. Best regrads should be with the recruiter email.
        11. Ask the candidate to click on the link or copy and paste it in the browser to add the event to their calendar.
        12. follow the above pattern to generate the email.
        
        Format the email with proper line breaks and spacing for readability.
        """
        subject = f"Interview Scheduled: {event_details['date']}"
        response = model.generate_content(prompt)
        email_body = response.text
        
        # Create mailto link with the generated content
        
        encoded_body = urllib.parse.quote(email_body)
        mailto_link =f'https://mail.google.com/mail/?view=cm&fs=1&to={st.session_state.candidate_email}&su={urllib.parse.quote(subject)}&body={encoded_body}'
    
        
        return mailto_link
    except Exception as e:
        default_email = f"""Dear Candidate,

Thank you for your interest in our organization. We are pleased to schedule your interview:

Date: {event_details['date']}
Time: {event_details['time']} IST
Duration: {event_details['duration']} minutes

To add this event to your calendar, please click the following link:
{sharing_link}

Please confirm receipt of this email and the calendar invitation.

Best regards,
{event_details['recruiter']}"""
        
        subject = f"Interview Scheduled: {event_details['date']}"
        encoded_body = urllib.parse.quote(default_email)
        mailto_link = f'https://mail.google.com/mail/?view=cm&fs=1&to={st.session_state.candidate_email}&su={urllib.parse.quote(subject)}&body={encoded_body}'
        
        return mailto_link

# Intial prompt of the BOT
if len(st.session_state.messages) == 0:
    st.write(INITIAL_PROMPT)

# Chat interface
if prompt := st.chat_input("Enter your response..."):
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Process user input and get AI response
    ai_response = process_user_input(prompt)
    
    # Add AI response to chat history
    st.session_state.messages.append({"role": "assistant", "content": ai_response})

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# Add confirmation buttons when needed
if st.session_state.step == "confirm_scheduling":
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("✅ Confirm and Schedule"):
            # Add button click to chat history
            with st.spinner("Scheduling interview...", show_time=True):
                st.session_state.messages.append({"role": "user", "content": "Confirm and Schedule"})
                
                selected_slot = st.session_state.selected_slot
                
                try:
                    # Ensure datetimes are in UTC for Google Calendar API
                    start_utc = selected_slot["start"].astimezone(timezone.utc)
                    end_utc = selected_slot["end"].astimezone(timezone.utc)
                    
                    # Create calendar event without attendees first
                    event = {
                        "summary": "Interview Meeting",
                        "description": f"""Interview scheduled by AI Interview Scheduler.

                                            Recruiter: {st.session_state.user_email}
                                            Candidate: {st.session_state.candidate_email}
                                            Duration: {st.session_state.interview_duration} minutes""",
                        "start": {
                            "dateTime": start_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                            "timeZone": "UTC"
                        },
                        "end": {
                            "dateTime": end_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                            "timeZone": "UTC"
                        }
                    }
                    
                    # Use the recruiter's email as the calendar ID
                    event_result = service.events().insert(
                        calendarId=st.session_state.user_email,  # Use recruiter's email as calendar ID
                        body=event,
                        sendUpdates="none"
                    ).execute()
                    
                    event_id = event_result.get('id')
                    html_link = event_result.get('htmlLink', '')
                    
                    # Generate the sharing link with proper encoding
                    start_time = start_utc.strftime("%Y%m%dT%H%M%SZ")
                    end_time = end_utc.strftime("%Y%m%dT%H%M%SZ")
                    
                    # URL encode the parameters
                    event_title = "Interview+Meeting"
                    description = f"Interview+with+{st.session_state.candidate_email}"
                    sharing_link = (
                        "https://calendar.google.com/calendar/render?"
                        f"action=TEMPLATE&"
                        f"text={event_title}&"
                        f"dates={start_time}/{end_time}&"
                        f"details={description}&"
                        "location=&"
                        "trp=false&"
                        "pli=1&"
                        "sf=true&"
                        "output=xml"
                    )
                    
                    # Prepare event details for Gemini
                    event_details = {
                        "date": st.session_state.interview_date.strftime('%A, %B %d, %Y'),
                        "time": f"{selected_slot['start'].strftime('%H:%M')} to {selected_slot['end'].strftime('%H:%M')} IST",
                        "duration": st.session_state.interview_duration,
                        "recruiter": st.session_state.user_email
                    }

                    # Generate email template with sharing link
                    mailto_link = generate_email_template(event_details, sharing_link)

                    confirmation_message = f"""✅ Interview has been scheduled successfully!

Event Details:
- Date: {st.session_state.interview_date.strftime('%A, %B %d, %Y')}
- Time: {selected_slot['start'].strftime('%H:%M')} to {selected_slot['end'].strftime('%H:%M')} IST
- Duration: {st.session_state.interview_duration} minutes
- Recruiter: {st.session_state.user_email}
- Candidate: {st.session_state.candidate_email}
- Customize and share manually: [Google Calendar Event]({html_link})

You can share the calendar event with the candidate using this link: [Send it]({mailto_link})

### :green[**The event has been added to your calendar.**]"""

                    st.session_state.messages.append({"role": "assistant", "content": confirmation_message})
                    st.session_state.step = "done"
                    st.rerun()
                    
                except Exception as e:
                    error_message = str(e)
                    st.error(f"Error creating calendar event: {error_message}")
                    if "403" in error_message:
                        st.error(f"""
                            Calendar access error. Please check:
                            1. You've shared your calendar ({st.session_state.user_email}) with: calendar-scheduler-bot@scheduling-bot-453903.iam.gserviceaccount.com
                            2. The service account has "Make changes and manage sharing" permissions
                            3. Try removing and re-adding the sharing permissions
                            """)
                    elif "404" in error_message:
                        st.error(f"""
                            Calendar not found error. This means:
                            1. The service account cannot access the calendar for {st.session_state.user_email}
                            2. Please make sure you've shared your calendar with the service account email
                            """)
                    else:
                        st.error("Please make sure you've shared your calendar with the service account email: calendar-scheduler-bot@scheduling-bot-453903.iam.gserviceaccount.com")
        
    with col2:
        if st.button("🔄 Change Date/Time"):
            # Add button click to chat history
            st.session_state.messages.append({"role": "user", "content": "Change Date/Time"})
            st.session_state.messages.append({"role": "assistant", "content": "Please provide a new preferred interview date. You can use natural language like 'next Monday' or 'March 25th'."})
            st.session_state.step = "interview_date"
            st.session_state.interview_date = None
            st.session_state.selected_slot = None
            st.rerun()
    
    with col3:
        if st.button("✏️ Modify Details"):
            # Add button click to chat history
            st.session_state.messages.append({"role": "user", "content": "Modify Details"})
            st.session_state.step = "modification_choice"
            st.rerun()

# Add modification options
if st.session_state.step == "modification_choice":
    st.write("What would you like to modify?")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📧 Recruiter Email"):
            # Add button click to chat history
            st.session_state.messages.append({"role": "user", "content": "Change Recruiter Email"})
            st.session_state.messages.append({"role": "assistant", "content": "Please provide the new recruiter email."})
            st.session_state.step = "initial"
            st.session_state.user_email = None
            st.rerun()
    
    with col2:
        if st.button("📧 Candidate Email"):
            # Add button click to chat history
            st.session_state.messages.append({"role": "user", "content": "Change Candidate Email"})
            st.session_state.messages.append({"role": "assistant", "content": "Please provide the new candidate email."})
            st.session_state.step = "candidate_email"
            st.session_state.candidate_email = None
            st.rerun()

# Add reset button when scheduling is done
if st.session_state.step == "done":
    if st.button("Schedule Another Interview"):
        # Add button click to chat history
        st.session_state.messages.append({"role": "user", "content": "Schedule Another Interview"})
        st.session_state.messages.append({"role": "assistant", "content": INITIAL_PROMPT})
        st.session_state.step = "initial"
        st.session_state.messages = []
        st.session_state.user_email = None
        st.session_state.candidate_email = None
        st.session_state.interview_date = None
        st.session_state.interview_duration = None
        st.session_state.free_slots = []
        st.session_state.selected_slot = None
        st.session_state.confirmation_state = None
        st.session_state.modification_type = None
        st.session_state.previous_step = None
        st.rerun()
