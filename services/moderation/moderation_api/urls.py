from django.urls import path

from .views import (
    BlockingReasonsView,
    ModerationEnqueueView,
    ModerationNextCardView,
    ProductEventsView,
    TicketApproveView,
    TicketBlockView,
)


urlpatterns = [
    path('events/product', ProductEventsView.as_view(), name='moderation-events-product-no-slash'),
    path('events/product/', ProductEventsView.as_view(), name='moderation-events-product'),
    path('product-moderation/get-next', ModerationNextCardView.as_view(), name='moderation-get-next-no-slash'),
    path('product-moderation/get-next/', ModerationNextCardView.as_view(), name='moderation-get-next'),
    path('product-moderation/enqueue', ModerationEnqueueView.as_view(), name='moderation-enqueue-no-slash'),
    path('product-moderation/enqueue/', ModerationEnqueueView.as_view(), name='moderation-enqueue'),
    path('tickets/<uuid:ticket_id>/approve', TicketApproveView.as_view(), name='moderation-ticket-approve-no-slash'),
    path('tickets/<uuid:ticket_id>/approve/', TicketApproveView.as_view(), name='moderation-ticket-approve'),
    path('tickets/<uuid:ticket_id>/block', TicketBlockView.as_view(), name='moderation-ticket-block-no-slash'),
    path('tickets/<uuid:ticket_id>/block/', TicketBlockView.as_view(), name='moderation-ticket-block'),
    path('blocking-reasons', BlockingReasonsView.as_view(), name='moderation-reasons-no-slash'),
    path('blocking-reasons/', BlockingReasonsView.as_view(), name='moderation-reasons'),
]
