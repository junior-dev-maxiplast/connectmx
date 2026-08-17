from .models import (userQueue,
                     concludedTasks,
                     PortalDemand)
from django.db import transaction
from django.db.models import F
from django.db.models import Max
from django.utils import timezone

def userQueueSaveInDatabase(request, data):
    user_code = request.user.userId
    extra_collaborators = data.pop("extra_collaborators", None)
    updatedPosition = (
        userQueue.objects.filter(user_code=user_code).aggregate(max_pos=Max("n_queue_position")).get("max_pos") or 0
    )
    updatedPosition = updatedPosition + 1
    data['n_queue_position'] = updatedPosition
    data['kanban_sort_order'] = updatedPosition
    data['user_code'] = user_code
    item = userQueue.objects.create(**data)
    if extra_collaborators is not None:
        item.extra_collaborators.set(extra_collaborators)

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
        PortalDemand.objects.filter(linked_queue_item=objectToDelete).update(
            status=PortalDemand.STATUS_PENDING,
            assigned_to=None,
            linked_queue_item=None,
            assumed_at=None,
            completed_at=None,
            updated_at=timezone.now(),
        )
        objectToDelete.delete()

        updateQueuePositions(user_code, position)       

def endQueueItem(request, ref_id):
    with transaction.atomic():
        user_code = request.user.userId
        objectCurrent = userQueue.objects.get(n_register=ref_id, user_code=user_code)
        position = objectCurrent.n_queue_position
        updateQueuePositions(user_code, position)
        linked_portal_demands = list(PortalDemand.objects.filter(linked_queue_item=objectCurrent))

        source_fields = {field.name for field in objectCurrent._meta.fields}
        target_fields = {
            field.name
            for field in concludedTasks._meta.fields
            if field.name not in {"id", "n_register", "d_conclusion_date", "d_conclusion_time"}
        }
        allowed_fields = source_fields.intersection(target_fields)

        data = {field_name: getattr(objectCurrent, field_name) for field_name in allowed_fields}

        concluded = concludedTasks.objects.create(**data)
        concluded.extra_collaborators.set(objectCurrent.extra_collaborators.all())

        for portal_demand in linked_portal_demands:
            portal_demand.status = PortalDemand.STATUS_COMPLETED
            portal_demand.completed_at = timezone.now()
            portal_demand.linked_queue_item = None
            portal_demand.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "linked_queue_item",
                    "updated_at",
                ]
            )

        objectCurrent.delete()



def updateQueuePositions(user_code, position):
    userQueue.objects.filter(user_code=user_code, n_queue_position__gt=position).update(
        n_queue_position=F("n_queue_position") - 1
    )
