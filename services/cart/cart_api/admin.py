from django.contrib import admin

from .models import Banner, Cart, CartItem, Collection, CollectionProduct, Favorite, ProductEventInbox, Subscription


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id", "session_id", "updated_at")
    search_fields = ("user_id", "session_id")


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "product_id", "sku_id", "quantity", "unavailable_reason", "updated_at")
    search_fields = ("sku_id", "product_id")


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id", "product_id", "added_at")
    search_fields = ("user_id", "product_id")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id", "product_id", "created_at")
    search_fields = ("user_id", "product_id")


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "is_active", "start_at", "end_at")
    search_fields = ("title", "link")


class CollectionProductInline(admin.TabularInline):
    model = CollectionProduct
    extra = 0


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "is_active", "start_date", "created_at")
    search_fields = ("title",)
    inlines = [CollectionProductInline]


@admin.register(ProductEventInbox)
class ProductEventInboxAdmin(admin.ModelAdmin):
    list_display = ("idempotency_key", "event", "product_id", "received_at")
    search_fields = ("idempotency_key", "product_id")
