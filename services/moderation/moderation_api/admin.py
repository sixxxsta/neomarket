from django.contrib import admin, messages
from django.db.models.deletion import ProtectedError

from .models import BlockingReason, ModerationCard, ModerationEvent


@admin.register(BlockingReason)
class BlockingReasonAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'hard_only', 'is_active', 'updated_at')
    list_filter = ('is_active', 'hard_only')
    search_fields = ('code', 'title')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('code', 'title', 'description', 'hard_only', 'is_active')}),
        ('Audit', {'fields': ('created_at', 'updated_at')}),
    )

    def delete_model(self, request, obj):
        try:
            super().delete_model(request, obj)
        except ProtectedError:
            self.message_user(
                request,
                'Нельзя удалить причину: на неё ссылаются карточки модерации. Установите is_active=False.',
                messages.ERROR,
            )

    def delete_queryset(self, request, queryset):
        blocked = 0
        for obj in queryset:
            if ModerationCard.objects.filter(decline_reason=obj).exists():
                blocked += 1
                continue
            obj.delete()
        if blocked:
            self.message_user(
                request,
                f'Пропущено {blocked} причин(ы) с историческими ссылками — деактивируйте через is_active.',
                messages.WARNING,
            )


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
