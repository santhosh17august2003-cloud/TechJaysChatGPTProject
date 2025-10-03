from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .forms import SignupForm, SignInForm
from .models import Chat
import json, os

# Gemini API
import google.generativeai as genai
from google.genai.errors import APIError

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ GEMINI_API_KEY not found in environment variables.")

# ---------------- UTILITY ---------------- #
def rename_session(user, old_session_name, first_user_message):
    """
    Generate a meaningful session title based on the first user message.
    """
    try:
        prompt = (
            f"Provide a concise descriptive title (3-6 words) "
            f"for this message. Do NOT use quotes or 'Title:'. "
            f"Message: '{first_user_message}'"
        )
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(
            temperature=0.3, max_output_tokens=20
        ))
        new_title = response.text.strip().replace('"', '').replace("'", "")
        if not new_title or len(new_title) < 3:
            new_title = first_user_message[:50].strip().capitalize()
        # Only update the session name for the current user and the specific old session
        Chat.objects.filter(user=user, session_name=old_session_name).update(session_name=new_title)
        return new_title
    except Exception:
        fallback_title = first_user_message[:50].strip().capitalize()
        return fallback_title

def get_gemini_response(prompt):
    try:
        if not GEMINI_API_KEY:
            return "Bot: API key not configured."
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.7, max_output_tokens=10000)
        )
        return response.text.strip() if response.candidates else "Bot: Could not generate a response."
    except APIError:
        return "Bot: API connection error."
    except Exception as e:
        return f"Bot: Error - {e}"

# ---------------- AUTH ---------------- #
# (signup, signin, signout functions are correct and not modified)
# ...

def signup(request):
    # ... (Your existing code)
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            full_name = form.cleaned_data['full_name']
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            username = email
            if User.objects.filter(username=username).exists():
                messages.error(request, "Email already registered!")
                return redirect('signup')
            User.objects.create_user(username=username, email=email, password=password, first_name=full_name)
            messages.success(request, "Account created successfully! Please Sign In.")
            return redirect('signin')
    else:
        form = SignupForm()
    return render(request, 'Chatapp/signup.html', {'form': form})

def signin(request):
    # ... (Your existing code)
    if request.method == "POST":
        form = SignInForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            username = email
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                return redirect('chat')
            else:
                messages.error(request, "Invalid email or password!")
                return redirect('signin')
    else:
        form = SignInForm()
    return render(request, 'Chatapp/signin.html', {'form': form})

def signout(request):
    logout(request)
    messages.success(request, "Logged out successfully!")
    return redirect('signin')

# ---------------- CHAT VIEWS ---------------- #
@login_required(login_url='signin')
def chat(request):
    """
    Display chat page and handle loading a session.
    Handles the "New Chat" button POST request.
    """
    
    # Logic for "New Chat" button
    if request.method == "POST":
        # Check for a specific 'action' field to confirm it's a new chat request
        if request.POST.get('action') == 'new_chat':
            # Create a new session with a unique name
            session_count = Chat.objects.filter(user=request.user).values('session_name').distinct().count()
            new_session_name = f"Chat {session_count + 1}"
            
            # Initial bot message
            Chat.objects.create(
                user=request.user,
                session_name=new_session_name,
                message="Hello! How can I assist you today?",
                sender="bot"
            )
            
            # Set the new session as current and redirect to a GET request
            request.session['current_session'] = new_session_name
            # Redirect forces a full page load to the newly created session, as intended for a "New Chat" button.
            return redirect('chat')
        
    # --- GET Logic: Load Chat Page ---
    
    # Get session name from URL (if a history item was clicked) or session, defaulting to 'Chat 1'
    session_name = request.GET.get('session') or request.session.get('current_session') or "Chat 1"
    
    # Ensure a default session 'Chat 1' is created if the user has no chats yet
    if session_name == "Chat 1" and not Chat.objects.filter(user=request.user, session_name="Chat 1").exists():
        Chat.objects.create(user=request.user, session_name="Chat 1", message="Hello! How can I assist you today?", sender="bot")

    # Update the current session in the user's session data
    request.session['current_session'] = session_name
    
    chats = Chat.objects.filter(user=request.user, session_name=session_name).order_by("timestamp")
    # Get all unique session names for the sidebar history
    sessions = Chat.objects.filter(user=request.user).exclude(session_name__isnull=True)\
                         .exclude(session_name="").values_list('session_name', flat=True).distinct()

    return render(request, 'Chatapp/chat.html', {"chats": chats, "session_name": session_name, "sessions": sessions})

@login_required
def profile(request):
    return render(request, 'Chatapp/profile.html')

# ---------------- AJAX ENDPOINTS ---------------- #

@csrf_exempt
@login_required
def getvalue(request):
    """
    Handles chat message submission via AJAX. This does NOT reload the page.
    This function is responsible for the in-place message update.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method"}, status=405) 

    session_name = request.session.get('current_session') or "Chat 1"

    try:
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()
    except json.JSONDecodeError:
        return JsonResponse({"reply": "Invalid JSON format."}, status=400)

    if not user_message:
        return JsonResponse({"reply": "Please type a message!"})

    # Check if this is the first user message in this session
    user_messages_count = Chat.objects.filter(user=request.user, session_name=session_name, sender="user").count()
    is_first_user_message = user_messages_count == 0

    # Save user message
    Chat.objects.create(user=request.user, message=user_message, sender="user", session_name=session_name)

    # Rename session if first message
    if is_first_user_message and session_name.startswith("Chat "):
        new_title = rename_session(request.user, session_name, user_message)
        request.session['current_session'] = new_title
        # CRITICAL: Update session_name variable for the bot's message creation
        session_name = new_title 

    # Generate bot response
    bot_reply = get_gemini_response(user_message)
    Chat.objects.create(user=request.user, message=bot_reply, sender="bot", session_name=session_name)

    # Return updated session_name and bot reply
    # The JSON response ensures no page reload happens.
    return JsonResponse({"reply": bot_reply, "session_name": session_name})

@login_required
def load_session(request, session_name):
    session_name = session_name.replace('%20', ' ')
    chats = Chat.objects.filter(user=request.user, session_name=session_name).order_by("timestamp")
    data = [{"sender": c.sender, "message": c.message} for c in chats]
    request.session['current_session'] = session_name
    return JsonResponse({"chats": data, "session_name": session_name})

@login_required
@csrf_exempt
def ajax_delete_session(request):
    # ... (Your existing code for deleting sessions is correct)
    if request.method != "POST":
        return JsonResponse({"deleted": False, "error": "Invalid request"}, status=400)

    try:
        data = json.loads(request.body)
        session_name = data.get("session_name", "").strip()
        if not session_name:
            return JsonResponse({"deleted": False, "error": "No session name provided"}, status=400)

        # Delete all chat messages for this session
        deleted_count, _ = Chat.objects.filter(user=request.user, session_name=session_name).delete()

        # Update current session to last available or default
        if request.session.get('current_session') == session_name:
            last_session = (
                Chat.objects.filter(user=request.user)
                .exclude(session_name__isnull=True)
                .exclude(session_name="")
                .values_list('session_name', flat=True)
                .last()
            )
            request.session['current_session'] = last_session or "Chat 1"

        return JsonResponse({"deleted": True, "session_name": session_name, "deleted_count": deleted_count})

    except Exception as e:
        return JsonResponse({"deleted": False, "error": str(e)}, status=500)