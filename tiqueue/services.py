from . import repositories as repository
from . import validators as validator
from django.db import transaction

def userQueueSaveItem(request, data):
    validator.userQueueValidateIntegrity(data)
    repository.userQueueSaveInDatabase(request, data)

def serviceUserQueueUpItem(request, id):
    repository.moveQueueItemUp(request, id)

def serviceUserQueueDropItem(request, id):
    repository.moveQueueItemDown(request, id)

def serviceDeleteQueueItem(request, id):
    repository.deleteQueueItem(request, id)

def serviceEndQueueItem(request, id):
    repository.endQueueItem(request, id)

