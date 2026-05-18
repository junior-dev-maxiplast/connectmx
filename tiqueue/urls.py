from django.urls import path
from . import views

urlpatterns = [

    path('', views.index, name='index'),

    path('queue/main/', views.queueMainPage, name='queueMainPage'),
    path('queue/main/item/<int:item_id>/', views.queueDemandDetailPage, name='queueDemandDetailPage'),
    path('queue/concluded/', views.queueConcludedPage, name='queueConcludedPage'),
    path('queue/user/', views.queueUserPage, name='queueUserPage'),
    path('queue/user/save/<int:id>', views.upQueuePosition, name='upQueuePosition'),
    path('queue/user/save/drop/<int:id>', views.dropQueuePosition, name='dropQueuePosition'),
    path('queue/user/duplicate/<int:id>/', views.duplicateQueueItem, name='duplicateQueueItem'),
    path('queue/user/delete/<int:id>', views.deleteQueueItem, name='deleteQueueItem'),
    path('queue/user/end/<int:id>', views.endQueueItem, name='endQueueItem'),
    path('queue/user/list/', views.listQueueUpdate, name='listQueueUpdate'),
    path('queue/user/item/<int:id>/', views.queueItemDetails, name='queueItemDetails'),
    path('queue/user/update/<int:id>/', views.updateQueueItem, name='updateQueueItem'),
    path('queue/user/current/<int:id>/', views.toggleCurrentTask, name='toggleCurrentTask'),
    path('queue/user/sync-sm/', views.syncQueueWithSM, name='syncQueueWithSM'),
    path('queue/user/link-project/<int:id>/', views.linkQueueItemToProject, name='linkQueueItemToProject'),
    path('queue/user/reorder/', views.reorderQueueItems, name='reorderQueueItems'),
    path('queue/user/kanban/column/create/', views.createMyQueueKanbanColumn, name='createMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/update/', views.updateMyQueueKanbanColumn, name='updateMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/delete/', views.deleteMyQueueKanbanColumn, name='deleteMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/reorder/', views.reorderMyQueueKanbanColumn, name='reorderMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/reorder-cards/', views.reorderMyQueueKanbanCards, name='reorderMyQueueKanbanCards'),
    path('queue/user/kanban/card/<int:item_id>/move/', views.moveMyQueueKanbanCard, name='moveMyQueueKanbanCard'),
    path('queue/user/details/<int:id>/', views.queueTaskDetailsList, name='queueTaskDetailsList'),
    path('queue/user/details/<int:id>/add/', views.queueTaskDetailsAdd, name='queueTaskDetailsAdd'),
    path('queue/user/details/<int:detail_id>/toggle/', views.queueTaskDetailsToggle, name='queueTaskDetailsToggle'),
    path('queue/user/details/<int:detail_id>/delete/', views.queueTaskDetailsDelete, name='queueTaskDetailsDelete'),
    path('queue/create/status/', views.listCreateStatus, name='listCreateStatus')
    ,
    path('queue/create/types/', views.manageTaskTypes, name='manageTaskTypes')
    ,
    path('queue/create/demand-templates/', views.manageDemandTemplates, name='manageDemandTemplates')
    ,
    path('hub/tools/', views.manageHubTools, name='manageHubTools')
    ,
    path('hub/tools/quick-add/', views.hubQuickAddItem, name='hubQuickAddItem')
    ,
    path('hub/tools/general/', views.manageHubTools, name='manageHubToolsGeneral')
    ,
    path('hub/tools/my/', views.manageMyHubTools, name='manageMyHubTools')
    ,
    path('knowledge/base/', views.knowledgeBasePage, name='knowledgeBasePage')
    ,
    path('knowledge/base/categories/', views.knowledgeCategoriesPage, name='knowledgeCategoriesPage')
    ,
    path('knowledge/base/entries/', views.knowledgeEntriesPage, name='knowledgeEntriesPage')
    ,
    path('knowledge/base/consult/', views.knowledgeConsultPage, name='knowledgeConsultPage')
    ,
    path('knowledge/base/entry/<int:entry_id>/', views.knowledgeEntryDetailPage, name='knowledgeEntryDetailPage')
    ,
    path('maxibot/', views.maxibotPage, name='maxibotPage')
    ,
    path('maxibot/ask/', views.maxibotAsk, name='maxibotAsk')
    ,
    path('system/settings/', views.systemSettingsPage, name='systemSettingsPage')
    ,
    path('updates/senior/', views.seniorUpdatesPage, name='seniorUpdatesPage'),
    path('updates/senior/<int:update_id>/inline/', views.seniorUpdatesInlineUpdate, name='seniorUpdatesInlineUpdate'),
    path('updates/wifi/', views.wifiVoucherPage, name='wifiVoucherPage'),
    path('updates/wifi/<int:voucher_id>/deliver/', views.wifiVoucherDeliver, name='wifiVoucherDeliver'),
    path('projects/', views.manageProjects, name='manageProjects')
    ,
    path('projects/catalog/', views.projectCatalogPage, name='projectCatalogPage')
    ,
    path('projects/<int:project_id>/board/', views.projectBoard, name='projectBoard'),
    path('projects/<int:project_id>/roadmap/', views.projectRoadmapView, name='projectRoadmapView'),
    path('projects/<int:project_id>/roadmap/add/', views.projectRoadmapItemCreate, name='projectRoadmapItemCreate'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/done/', views.projectRoadmapItemConclude, name='projectRoadmapItemConclude'),
    path('projects/<int:project_id>/card/create/', views.projectCardCreate, name='projectCardCreate'),
    path('projects/card/<int:card_id>/move/', views.projectCardMove, name='projectCardMove'),
    path('projects/card/<int:card_id>/delete/', views.projectCardDelete, name='projectCardDelete')

    

]
