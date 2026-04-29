from django.contrib.auth import get_user_model

User = get_user_model()

class CustomAuthBackend:
    def authenticate(self, request, username=None, password=None, **kwargs):
        user = None

        try:
            user = User.objects.get(userId=username)
        except User.DoesNotExist:
            pass

        if user is None:
            try:
                user = User.objects.get(email=username)
            except User.DoesNotExist:
                pass

        if user is None:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                pass

        if user and user.check_password(password):
            return user
        
        return None
    
    def get_user(self, userId):
        User = get_user_model()

        try: 
            return get_user_model().objects.get(pk=userId)
        except get_user_model().DoesNotExist:
            return None


