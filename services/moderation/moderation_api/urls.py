from django.urls import path

from .views import (
    BlockingReasonsView,
    ModerationEnqueueView,
    ModerationNextCardView,
    ProductApproveView,
    ProductDeclineView,
    ProductEventsView,
)


urlpatterns = [
    path('events/product', ProductEventsView.as_view(), name='moderation-events-product-no-slash'),
    path('events/product/', ProductEventsView.as_view(), name='moderation-events-product'),
    path('product-moderation/get-next', ModerationNextCardView.as_view(), name='moderation-get-next-no-slash'),
    path('product-moderation/get-next/', ModerationNextCardView.as_view(), name='moderation-get-next'),
    path('product-moderation/enqueue', ModerationEnqueueView.as_view(), name='moderation-enqueue-no-slash'),
    path('product-moderation/enqueue/', ModerationEnqueueView.as_view(), name='moderation-enqueue'),
    path('products/<uuid:id>/approve', ProductApproveView.as_view(), name='moderation-approve-no-slash'),
    path('products/<uuid:id>/approve/', ProductApproveView.as_view(), name='moderation-approve'),
    path('products/<uuid:id>/decline', ProductDeclineView.as_view(), name='moderation-decline-no-slash'),
    path('products/<uuid:id>/decline/', ProductDeclineView.as_view(), name='moderation-decline'),
    path('product-blocking-reasons', BlockingReasonsView.as_view(), name='moderation-reasons-no-slash'),
    path('product-blocking-reasons/', BlockingReasonsView.as_view(), name='moderation-reasons'),
]
