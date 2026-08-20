from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [

    path('', views.index, name='index'),
    path('dashes/', views.dashesHomePage, name='dashesHome'),
    path('dashes/entrar/', views.dashesLoginPage, name='dashesLoginPage'),
    path('dashes/sair/', views.dashesLogoutPage, name='dashesLogoutPage'),
    path('dashes/dna-cliente/', views.dashesCustomerDnaPage, name='dashesCustomerDnaPage'),
    path('dashes/bi-ti/', views.dashesItBiPage, name='dashesItBiPage'),
    path('dashes/bi-ti/insights/preparar/', views.itBiPrepareInsights, name='itBiPrepareInsights'),
    path('dashes/bi-ti/insights/solicitar-ia/', views.itBiRequestAiInsights, name='itBiRequestAiInsights'),
    path('dashes/bi-ti/insights/payload/', views.itBiInsightPayloadApi, name='itBiInsightPayloadApi'),
    path('dashes/bi-ti/exportar/pdf/', views.itBiExportPdf, name='itBiExportPdf'),
    path('clientes/dna/', views.customerDnaPage, name='customerDnaPage'),
    path('clientes/dna/api/busca/', views.customerDnaSearchApi, name='customerDnaSearchApi'),
    path('clientes/dna/insights/preparar/', views.customerDnaPrepareInsights, name='customerDnaPrepareInsights'),
    path('clientes/dna/insights/solicitar-ia/', views.customerDnaRequestAiInsights, name='customerDnaRequestAiInsights'),
    path('clientes/dna/<int:customer_id>/insights/payload/', views.customerDnaInsightPayloadApi, name='customerDnaInsightPayloadApi'),
    path('clientes/dna/<int:customer_id>/exportar/pdf/', views.customerDnaExportPdf, name='customerDnaExportPdf'),
    # Short, shareable entry point for requesters ("/portal/"). Keeps the real
    # portal URLs stable while giving end users a single link to bookmark.
    path('portal/', RedirectView.as_view(pattern_name='portalDemandPage', permanent=False), name='portalEntry'),
    path('portal/entrar/', views.portalLoginPage, name='portalLoginPage'),
    path('hub/', views.hubPage, name='hubPage'),
    path('portal/chamados/', views.portalDemandPage, name='portalDemandPage'),
    path('portal/chamados/abrir/', views.portalDemandCreatePage, name='portalDemandCreatePage'),
    path('portal/chamados/abrir/insights/', views.portalDemandInsightsApi, name='portalDemandInsightsApi'),
    path('portal/chamados/api/ai/<int:demand_id>/routing-context/', views.portalDemandAiRoutingContextApi, name='portalDemandAiRoutingContextApi'),
    path('portal/chamados/api/ai/<int:demand_id>/apply-routing/', views.portalDemandAiRoutingApplyApi, name='portalDemandAiRoutingApplyApi'),
    path('portal/chamados/minhas/', views.portalMyDemandsPage, name='portalMyDemandsPage'),
    path('portal/chamados/pendentes/', views.portalPendingDemandsPage, name='portalPendingDemandsPage'),
    path('portal/chamados/configuracoes/campos/', views.portalDemandFieldsConfigPage, name='portalDemandFieldsConfigPage'),
    path('portal/chamados/configuracoes/sla/', views.portalDemandSlaConfigPage, name='portalDemandSlaConfigPage'),
    path('portal/chamados/configuracoes/respostas/', views.portalDemandResponsesConfigPage, name='portalDemandResponsesConfigPage'),
    path('portal/chamados/configuracoes/solicitantes/', views.portalRequesterAdminPage, name='portalRequesterAdminPage'),
    path('portal/chamados/codigo/<str:demand_code>/', views.portalDemandCodeDetailPage, name='portalDemandCodeDetailPage'),
    path('portal/chamados/<int:demand_id>/', views.portalDemandDetailPage, name='portalDemandDetailPage'),
    path('portal/chamados/<int:demand_id>/assumir/', views.portalDemandAssume, name='portalDemandAssume'),
    path('portal/chamados/assumir-selecionadas/', views.portalDemandBulkAssume, name='portalDemandBulkAssume'),

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
    path('queue/user/properties/', views.queueUserPropertyPayloadData, name='queueUserPropertyPayloadData'),
    path('queue/user/item/<int:id>/', views.queueItemDetails, name='queueItemDetails'),
    path('queue/user/create-inline/', views.createQueueItemInline, name='createQueueItemInline'),
    path('queue/user/update/<int:id>/', views.updateQueueItem, name='updateQueueItem'),
    path('queue/user/gantt-range/<int:id>/', views.updateQueueGanttRange, name='updateQueueGanttRange'),
    path('queue/user/current/<int:id>/', views.toggleCurrentTask, name='toggleCurrentTask'),
    path('queue/user/custom-columns/create/', views.createUserQueueCustomColumn, name='createUserQueueCustomColumn'),
    path('queue/user/custom-columns/<int:column_id>/options/create/', views.createUserQueueCustomColumnOption, name='createUserQueueCustomColumnOption'),
    path('queue/user/custom-columns/options/<int:option_id>/delete/', views.deleteUserQueueCustomColumnOption, name='deleteUserQueueCustomColumnOption'),
    path('queue/user/field-options/create/', views.createUserQueueFieldOption, name='createUserQueueFieldOption'),
    path('queue/user/field-options/<int:option_id>/delete/', views.deleteUserQueueFieldOption, name='deleteUserQueueFieldOption'),
    path('queue/user/custom-value/<int:id>/', views.setUserQueueCustomValue, name='setUserQueueCustomValue'),
    path('queue/user/views/save/', views.saveUserQueueView, name='saveUserQueueView'),
    path('queue/user/views/<int:view_id>/delete/', views.deleteUserQueueView, name='deleteUserQueueView'),
    path('queue/user/task-type/quick-create/', views.createTaskTypeQuick, name='createTaskTypeQuick'),
    path('queue/user/sync-sm/', views.syncQueueWithSM, name='syncQueueWithSM'),
    path('queue/user/link-project/<int:id>/', views.linkQueueItemToProject, name='linkQueueItemToProject'),
    path('queue/user/reorder/', views.reorderQueueItems, name='reorderQueueItems'),
    path('queue/user/kanban/column/create/', views.createMyQueueKanbanColumn, name='createMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/update/', views.updateMyQueueKanbanColumn, name='updateMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/delete/', views.deleteMyQueueKanbanColumn, name='deleteMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/reorder/', views.reorderMyQueueKanbanColumn, name='reorderMyQueueKanbanColumn'),
    path('queue/user/kanban/column/<int:column_id>/reorder-cards/', views.reorderMyQueueKanbanCards, name='reorderMyQueueKanbanCards'),
    path('queue/user/kanban/card/<int:item_id>/move/', views.moveMyQueueKanbanCard, name='moveMyQueueKanbanCard'),
    path('queue/user/agenda/', views.myAgendaPage, name='myAgendaPage'),
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
    path('hub/tetris/highscores/', views.maxiTetrisHighscores, name='maxiTetrisHighscores')
    ,
    path('hub/tetris/highscores/submit/', views.maxiTetrisSubmitScore, name='maxiTetrisSubmitScore')
    ,
    path('pomodoro/', views.pomodoroPage, name='pomodoroPage')
    ,
    path('pomodoro/session/save/', views.pomodoroSaveSession, name='pomodoroSaveSession')
    ,
    path('pomodoro/session/<int:session_id>/delete/', views.pomodoroDeleteSession, name='pomodoroDeleteSession')
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
    path('system/services/', views.serviceAgentPage, name='serviceAgentPage')
    ,
    path('system/services/api/list/', views.serviceAgentList, name='serviceAgentList')
    ,
    path('system/services/api/action/<str:service_name>/<str:action>/', views.serviceAgentAction, name='serviceAgentAction')
    ,
    path('system/services/api/erp/cleanup-users/', views.serviceAgentErpCleanupUsers, name='serviceAgentErpCleanupUsers')
    ,
    path('data-modeler/', views.dataModelerPage, name='dataModelerPage'),
    path('data-modeler/api/state/', views.dataModelerState, name='dataModelerState'),
    path('data-modeler/api/launch/create/', views.dataModelerLaunchCreate, name='dataModelerLaunchCreate'),
    path('data-modeler/api/table/create/', views.dataModelerTableCreate, name='dataModelerTableCreate'),
    path('data-modeler/api/table/<int:table_id>/update/', views.dataModelerTableUpdate, name='dataModelerTableUpdate'),
    path('data-modeler/api/table/<int:table_id>/field/create/', views.dataModelerFieldCreate, name='dataModelerFieldCreate'),
    path('data-modeler/api/relation/create/', views.dataModelerRelationCreate, name='dataModelerRelationCreate'),
    path('data-modeler/api/sql/generate/', views.dataModelerGenerateOracleSql, name='dataModelerGenerateOracleSql'),
    path('updates/senior/', views.seniorUpdatesPage, name='seniorUpdatesPage'),
    path('updates/senior/<int:update_id>/inline/', views.seniorUpdatesInlineUpdate, name='seniorUpdatesInlineUpdate'),
    path('updates/wifi/', views.wifiVoucherPage, name='wifiVoucherPage'),
    path('contracts/', views.contractsPage, name='contractsPage'),
    path('updates/wifi/<int:voucher_id>/deliver/', views.wifiVoucherDeliver, name='wifiVoucherDeliver'),
    path('maintenance/', views.maintenancePage, name='maintenancePage'),
    path('maintenance/catalog/', views.maintenanceCatalogPage, name='maintenanceCatalogPage'),
    path('maintenance/outages/', views.maintenanceOutagePage, name='maintenanceOutagePage'),
    path('maintenance/schedules/', views.maintenanceSchedulePage, name='maintenanceSchedulePage'),
    path('maintenance/calendar/', views.maintenanceCalendarPage, name='maintenanceCalendarPage'),
    path('projects/', views.manageProjects, name='manageProjects'),
    path('projects/calendar/', views.projectCalendarPage, name='projectCalendarPage'),
    path('projects/timeline/', views.projectTimelinePage, name='projectTimelinePage'),
    path('projects/catalog/', views.projectCatalogPage, name='projectCatalogPage'),
    path('projects/catalog/concluded/', views.projectCatalogConcludedPage, name='projectCatalogConcludedPage'),
    path('projects/<int:project_id>/export/pdf/', views.projectCatalogExportPdf, name='projectCatalogExportPdf'),
    path('projects/<int:project_id>/board/', views.projectBoard, name='projectBoard'),
    path('projects/<int:project_id>/roadmap/', views.projectRoadmapView, name='projectRoadmapView'),
    path('projects/<int:project_id>/roadmap/milestones/add/', views.projectMilestoneCreate, name='projectMilestoneCreate'),
    path('projects/<int:project_id>/roadmap/milestones/<int:milestone_id>/update/', views.projectMilestoneUpdate, name='projectMilestoneUpdate'),
    path('projects/<int:project_id>/roadmap/milestones/<int:milestone_id>/toggle/', views.projectMilestoneToggle, name='projectMilestoneToggle'),
    path('projects/<int:project_id>/roadmap/milestones/<int:milestone_id>/move/', views.projectMilestoneMove, name='projectMilestoneMove'),
    path('projects/<int:project_id>/roadmap/add/', views.projectRoadmapItemCreate, name='projectRoadmapItemCreate'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/update/', views.projectRoadmapItemUpdate, name='projectRoadmapItemUpdate'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/done/', views.projectRoadmapItemConclude, name='projectRoadmapItemConclude'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/reopen/', views.projectRoadmapItemReopen, name='projectRoadmapItemReopen'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/subtasks/add/', views.projectRoadmapSubtaskCreate, name='projectRoadmapSubtaskCreate'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/subtasks/<int:subtask_id>/update/', views.projectRoadmapSubtaskUpdate, name='projectRoadmapSubtaskUpdate'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/subtasks/<int:subtask_id>/toggle/', views.projectRoadmapSubtaskToggle, name='projectRoadmapSubtaskToggle'),
    path('projects/<int:project_id>/roadmap/item/<int:item_id>/subtasks/<int:subtask_id>/delete/', views.projectRoadmapSubtaskDelete, name='projectRoadmapSubtaskDelete'),
    path('projects/<int:project_id>/card/create/', views.projectCardCreate, name='projectCardCreate'),
    path('projects/card/<int:card_id>/move/', views.projectCardMove, name='projectCardMove'),
    path('projects/card/<int:card_id>/delete/', views.projectCardDelete, name='projectCardDelete')

    

]
