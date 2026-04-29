from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import redirect, render

from accounts.models import User


def index(request):
    return render(request, "tiqueue/index.html")


def loginPage(request):
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        accessField = request.POST.get("login")
        passwordField = request.POST.get("password")

        user = authenticate(request, username=accessField, password=passwordField)
        if user:
            login(request, user)
            return redirect("index")

    return render(request, "general/login.html")


def logoutPage(request):
    if request.method == "POST":
        logout(request)
    return redirect("loginPage")


@login_required
def createUser(request):
    error_message = None
    success_message = None

    if request.method == "POST":
        form_id = (request.POST.get("form_id") or "create").strip()

        if form_id == "edit":
            user_pk = request.POST.get("user_pk")
            user = User.objects.filter(pk=user_pk).first()
            if not user:
                error_message = "Usuario nao encontrado."
            else:
                user_id = (request.POST.get("userId") or "").strip()
                username = (request.POST.get("username") or "").strip()
                email = (request.POST.get("email") or "").strip()
                name_user = (request.POST.get("nameUser") or "").strip()
                id_sm = (request.POST.get("id_sm") or "").strip()
                password = (request.POST.get("password") or "").strip()

                if not user_id or not username or not email or not name_user:
                    error_message = "Preencha os campos obrigatorios para editar o usuario."
                elif User.objects.exclude(pk=user.pk).filter(userId=user_id).exists():
                    error_message = "Ja existe um usuario com esta matricula."
                elif User.objects.exclude(pk=user.pk).filter(username=username).exists():
                    error_message = "Ja existe um usuario com este login."
                elif User.objects.exclude(pk=user.pk).filter(email=email).exists():
                    error_message = "Ja existe um usuario com este e-mail."
                else:
                    user.userId = user_id
                    user.username = username
                    user.email = email
                    user.nameUser = name_user
                    user.id_sm = id_sm or None
                    if password:
                        user.set_password(password)
                    user.save()
                    success_message = "Usuario atualizado com sucesso."
        else:
            name_user = (request.POST.get("nameUser") or "").strip()
            user_id = (request.POST.get("userId") or "").strip()
            email = (request.POST.get("email") or "").strip()
            username = (request.POST.get("username") or "").strip()
            id_sm = (request.POST.get("id_sm") or "").strip()
            password = (request.POST.get("password") or "").strip()

            if not name_user or not user_id or not email or not username or not password:
                error_message = "Preencha todos os campos para cadastrar o usuario."
            else:
                try:
                    User.objects.create_user(
                        userId=user_id,
                        username=username,
                        email=email,
                        nameUser=name_user,
                        id_sm=id_sm or None,
                        password=password,
                    )
                    success_message = "Usuario cadastrado com sucesso."
                except IntegrityError:
                    error_message = "Ja existe um usuario com matricula, login ou e-mail informado."

    users = User.objects.all().order_by("nameUser", "username")
    return render(
        request,
        "general/createUser.html",
        {
            "users": users,
            "error_message": error_message,
            "success_message": success_message,
        },
    )
