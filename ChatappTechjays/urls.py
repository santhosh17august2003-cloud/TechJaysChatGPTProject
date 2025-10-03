from django.urls import path
from ChatappTechjays import views

urlpatterns = [
	path("chat/", views.chat, name="chat"),
    # New AJAX URLs for session management
    path('load_session/<path:session_name>/', views.load_session, name='load_session'),
    path('ajax_delete_session/', views.ajax_delete_session, name='ajax_delete_session'),    
	path("", views.signin, name="signin"),
	path("signup/", views.signup, name="signup"),
    path("signout/", views.signout, name="signout"),
    path('getvalue/', views.getvalue, name='getvalue'),
]