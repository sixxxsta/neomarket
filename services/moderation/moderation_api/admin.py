from django.contrib import admin

from .models import BlockingReason, ModerationCard, ModerationEvent


@admin.register(BlockingReason)
class BlockingReasonAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'title')


@admin.register(ModerationCard)
class ModerationCardAdmin(admin.ModelAdmin):
    list_display = ('id', 'product_id', 'event_type', 'queue_status', 'assigned_to', 'decided_by', 'created_at')
    list_filter = ('queue_status', 'event_type')
    search_fields = ('product_id', 'assigned_to', 'decided_by')


@admin.register(ModerationEvent)
class ModerationEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'event_type', 'product_id', 'created_at')
    list_filter = ('event_type',)
    search_fields = ('product_id',)
