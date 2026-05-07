from .models import (userQueue,
                     concludedTasks)
from django.db import transaction
from django.db.models import F
from django.db.models import Max

def userQueueSaveInDatabase(request, data):
    user_code = request.user.userId
    updatedPosition = (
        userQueue.objects.filter(user_code=user_code).aggregate(max_pos=Max("n_queue_position")).get("max_pos") or 0
    )
    updatedPosition = updatedPosition + 1
    data['n_queue_position'] = updatedPosition
    data['user_code'] = user_code
    userQueue.objects.create(**data)

def moveQueueItemUp(request, ref_id):
    with transaction.atomic():
        user_code = request.user.userId
        newPosition = userQueue.objects.get(n_register=ref_id, user_code=user_code)
        oldPosition = (
            userQueue.objects.filter(user_code=user_code, n_queue_position__lt=newPosition.n_queue_position)
            .order_by("-n_queue_position")
            .first()
        )

        if oldPosition:
            newPosition.n_queue_position, oldPosition.n_queue_position = oldPosition.n_queue_position, newPosition.n_queue_position
            newPosition.save()
            oldPosition.save()

def moveQueueItemDown(request, ref_id):
    with transaction.atomic():
        user_code = request.user.userId
        newPosition = userQueue.objects.get(n_register=ref_id, user_code=user_code)
        oldPosition = (
            userQueue.objects.filter(user_code=user_code, n_queue_position__gt=newPosition.n_queue_position)
            .order_by("n_queue_position")
            .first()
        )

        if oldPosition:
            newPosition.n_queue_position, oldPosition.n_queue_position = oldPosition.n_queue_position, newPosition.n_queue_position
            newPosition.save()
            oldPosition.save()

def deleteQueueItem(request, ref_id):
    with transaction.atomic():
        user_code = request.user.userId
        objectToDelete = userQueue.objects.get(n_register=ref_id, user_code=user_code)
        position = objectToDelete.n_queue_position
        objectToDelete.delete()

        updateQueuePositions(user_code, position)       

def endQueueItem(request, ref_id):
    with transaction.atomic():
        user_code = request.user.userId
        objectCurrent = userQueue.objects.get(n_register=ref_id, user_code=user_code)
        position = objectCurrent.n_queue_position
        updateQueuePositions(user_code, position)

        source_fields = {field.name for field in objectCurrent._meta.fields}
        target_fields = {
            field.name
            for field in concludedTasks._meta.fields
            if field.name not in {"id", "n_register", "d_conclusion_date", "d_conclusion_time"}
        }
        allowed_fields = source_fields.intersection(target_fields)

        data = {field_name: getattr(objectCurrent, field_name) for field_name in allowed_fields}

        concludedTasks.objects.create(**data)

        objectCurrent.delete()



def updateQueuePositions(user_code, position):
    userQueue.objects.filter(user_code=user_code, n_queue_position__gt=position).update(
        n_queue_position=F("n_queue_position") - 1
    )
