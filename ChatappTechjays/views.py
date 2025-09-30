from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from .forms import SignupForm, SignInForm
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import google.generativeai as genai
from google.genai.errors import APIError 
import os
from .models import Chat

# Configure Gemini API key from environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("⚠️ GEMINI_API_KEY not found in environment variables. Please set it securely.")
else:
    genai.configure(api_key=GEMINI_API_KEY)

@login_required(login_url='signin') 
def chat(request):
    return render(request, 'Chatapp/chat.html')

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

            User.objects.create_user(
                username=username, email=email, password=password, first_name=full_name
            )
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

@login_required
def profile(request):
    return render(request, 'Chatapp/profile.html')


def get_gemini_response(message):
    try:
        if not GEMINI_API_KEY:
            return "Bot: Sorry, the API key is not configured. Please contact the administrator."
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            message,
            generation_config=genai.GenerationConfig(
                temperature=0.7,
                max_output_tokens=10000
            )
        )
        if response.candidates and response.candidates[0].content.parts:
            return response.text.strip()
        else:
            finish_reason = response.candidates[0].finish_reason.name if response.candidates else "NO_CANDIDATE"
            
            print(f"Gemini API Content Filtered/Stopped. Reason: {finish_reason}")
            if finish_reason == 'SAFETY':
                return "Bot: Sorry, I can't answer that query as it violates safety guidelines. Please try a different question."
            else:
                return "Bot: Sorry, I couldn't generate a valid response for that. Please try again later."
        # ---------------------

    except APIError as e:
        print(f"Gemini API Error: {e}")
        return "Bot: A connection or API error occurred. Please contact the administrator."
    except Exception as e:
        print(f"Gemini API Unhandled Error: {e}")
        return "Bot: Sorry, I couldn't process your request. Please try again later."


@csrf_exempt
@login_required # <-- RECTIFIED: This decorator is essential for security and functionality.
def getvalue(request):
    if request.method == "POST":
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()

        if not user_message:
            return JsonResponse({"reply": "Please type something!"}) 
        
        # request.user is safe and guaranteed to be the authenticated user due to @login_required
        Chat.objects.create(user=request.user, message=user_message, sender="user")
        bot_reply = get_gemini_response(user_message)
        Chat.objects.create(user=request.user, message=bot_reply, sender="bot")

        return JsonResponse({"reply": bot_reply})

    return JsonResponse({"error": "Invalid request"}, status=400)

@login_required
def chat_history(request):
    chats = Chat.objects.filter(user=request.user).order_by('timestamp')
    return render(request, 'Chatapp/chathistory.html', {'chats': chats})