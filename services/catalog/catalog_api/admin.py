from django.contrib import admin

from .models import Category, Product, Sku


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "parent")
    search_fields = ("name", "slug")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "status", "category", "created_at")
    list_filter = ("status", "category")
    search_fields = ("title",)


@admin.register(Sku)
class SkuAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "product", "price", "active_quantity")
    search_fields = ("name",)
