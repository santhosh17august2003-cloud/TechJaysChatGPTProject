from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from .forms import SignupForm, SignInForm
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json, os

from .models import Chat

# Gemini API
import google.generativeai as genai
from google.genai.errors import APIError

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ GEMINI_API_KEY not found in environment variables.")


# ---------------- UTILITY: RENAME SESSION ---------------- #
def rename_session(user, old_session_name, first_user_message):
    """
    Use Gemini to generate a session title based on the first user message.
    """
    try:
        # Prompt asks Gemini to create a meaningful descriptive title
        prompt = (
            f"Provide a concise and descriptive title (3-6 words) "
            f"for the following user message. Do NOT use quotation marks or 'Title:'. "
            f"Message: '{first_user_message}'"
        )

        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(temperature=0.3, max_output_tokens=20)
        )

        # Extract text and clean it
        new_title = response.text.strip().replace('"', '').replace("'", "")

        # Fallback: If Gemini fails to produce meaningful title, just use first words of message
        if not new_title or len(new_title) < 3:
            new_title = first_user_message[:50].strip().capitalize()
            if len(new_title) > 50:
                new_title = new_title[:47] + "..."

        # Update all messages in this session
        Chat.objects.filter(user=user, session_name=old_session_name).update(session_name=new_title)

        return new_title

    except Exception:
        # If API fails, fallback to the first few words of the user message
        fallback_title = first_user_message[:50].strip().capitalize()
        if len(fallback_title) > 50:
            fallback_title = fallback_title[:47] + "..."
        return fallback_title


# ---------------- AUTH ---------------- #
def signup(request):
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


# ---------------- MAIN CHAT ---------------- #
@login_required(login_url='signin')
def chat(request):
    """Display chat page and handle new chat creation"""
    # Create new chat session
    if request.method == "POST":
        # Get the count of distinct sessions to name the new one
        session_count = Chat.objects.filter(user=request.user).values('session_name').distinct().count()
        new_session_name = f"Chat {session_count + 1}"
        request.session['current_session'] = new_session_name

        # Initial bot message
        Chat.objects.create(
            user=request.user,
            session_name=new_session_name,
            message="Hello! How can I assist you today?",
            sender="bot"
        )
        # Note: We must reload to the new session, so a redirect is necessary
        return redirect('chat') 

    # GET: load chats for current session
    session_name = request.GET.get('session') or request.session.get('current_session') or "Chat 1"
    
    # If the default session doesn't exist, set it up
    if session_name == "Chat 1" and not Chat.objects.filter(user=request.user, session_name="Chat 1").exists():
        Chat.objects.create(
            user=request.user,
            session_name="Chat 1",
            message="Hello! How can I assist you today?",
            sender="bot"
        )
    
    request.session['current_session'] = session_name

    chats = Chat.objects.filter(user=request.user, session_name=session_name).order_by("timestamp")
    # Exclude None and empty string session names 
    sessions = Chat.objects.filter(user=request.user).exclude(session_name__isnull=True).exclude(session_name="").values_list('session_name', flat=True).distinct()

    return render(request, 'Chatapp/chat.html', {
        "chats": chats,
        "session_name": session_name,
        "sessions": sessions
    })


@login_required
def profile(request):
    return render(request, 'Chatapp/profile.html')


# Note: chat_history view is likely no longer used since chat() handles the sidebar logic
@login_required
def chat_history(request):
    sessions = Chat.objects.filter(user=request.user).exclude(session_name__isnull=True).exclude(session_name="").values_list('session_name', flat=True).distinct()
    return render(request, 'Chatapp/chathistory.html', {'sessions': sessions})


# ---------------- GEMINI RESPONSE ---------------- #
def get_gemini_response(prompt):
    try:
        if not GEMINI_API_KEY:
            return "Bot: API key not configured."
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(temperature=0.7, max_output_tokens=10000))
        return response.text.strip() if response.candidates and response.candidates[0].content.parts else "Bot: Could not generate a valid response."
    except APIError:
        return "Bot: API connection error."
    except Exception as e:
        return f"Bot: Error - {e}"


# ---------------- MESSAGE ONLY ---------------- #
@csrf_exempt
@login_required
def getvalue(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=400)

    # --- Session Retrieval ---
    session_name = request.session.get('current_session') or "Chat 1"
    
    try:
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()
    except json.JSONDecodeError:
        return JsonResponse({"reply": "Invalid JSON."})

    if not user_message:
        return JsonResponse({"reply": "Please type a message!"})

    is_first_user_message = False
    
    # Check if this is the user's first message in a new default-named session
    if session_name.startswith("Chat ") and Chat.objects.filter(user=request.user, session_name=session_name, sender="user").count() == 0:
        is_first_user_message = True

    # --- Message Saving (User) ---
    Chat.objects.create(user=request.user, message=user_message, sender="user", session_name=session_name)

    # --- Session Renaming Logic ---
    if is_first_user_message:
        new_title = rename_session(request.user, session_name, user_message)
        request.session['current_session'] = new_title # Update session variable
        session_name = new_title # Update local variable

    # --- Get Bot Response ---
    bot_reply = get_gemini_response(user_message)
    Chat.objects.create(user=request.user, message=bot_reply, sender="bot", session_name=session_name)

    # We send back the potentially new session name for the frontend to update the sidebar
    return JsonResponse({"reply": bot_reply, "session_name": session_name})


# ---------------- AJAX: LOAD SESSION ---------------- #
# Note: This is an AJAX endpoint, not a standard view
@login_required
def load_session(request, session_name):
    """Return full old chat history as JSON"""
    # URL encoded names can contain special characters, so we un-quote them
    session_name = session_name.replace('%20', ' ')
    chats = Chat.objects.filter(user=request.user, session_name=session_name).order_by("timestamp")
    data = [{"sender": c.sender, "message": c.message} for c in chats]
    request.session['current_session'] = session_name
    return JsonResponse({"chats": data, "session_name": session_name})


# ---------------- AJAX: DELETE SESSION ---------------- #
@login_required
@csrf_exempt
def ajax_delete_session(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            session_name = data.get("session_name", "").strip()

            if not session_name:
                return JsonResponse({"deleted": False, "error": "No session name provided"}, status=400)

            # Delete all chats associated with the session name
            Chat.objects.filter(user=request.user, session_name=session_name).delete()

            # Clear current session if the deleted one was active
            if request.session.get('current_session') == session_name:
                request.session['current_session'] = None

            return JsonResponse({"deleted": True, "session_name": session_name})
        except Exception as e:
            return JsonResponse({"deleted": False, "error": str(e)}, status=500)

    return JsonResponse({"deleted": False, "error": "Invalid request"}, status=400)