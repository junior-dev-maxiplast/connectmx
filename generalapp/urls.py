from django.urls import path
from . import views

urlpatterns = [

    path('', views.index, name='index'),

    path('main/login/', views.loginPage, name='loginPage'),
    path('accounts/login/', views.loginPage, name='loginPage'),
    path('main/logout/', views.logoutPage, name='logoutPage'),
    path('main/create/user', views.createUser, name='createUser'),

]
