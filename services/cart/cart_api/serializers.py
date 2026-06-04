from rest_framework import serializers

from .models import BannerEvent, CartItem, Favorite, Subscription


class CartItemSerializer(serializers.ModelSerializer):
    item_id = serializers.UUIDField(source="id", read_only=True)

    class Meta:
        model = CartItem
        fields = ["item_id", "sku_id", "quantity", "updated_at"]


class AddCartItemRequestSerializer(serializers.Serializer):
    sku_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


class UpdateCartItemRequestSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)


class FavoriteMutationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Favorite
        fields = ["product_id", "user_id", "added_at"]


class FavoriteListItemSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()

    class Meta:
        model = Favorite
        fields = ["product", "added_at"]

    def get_product(self, obj):
        return {"id": str(obj.product_id)}


class SubscribeRequestSerializer(serializers.Serializer):
    notify_on = serializers.ListField(
        child=serializers.ChoiceField(choices=[Subscription.NotifyEvent.IN_STOCK, Subscription.NotifyEvent.PRICE_DOWN]),
        allow_empty=False,
    )


class BannerEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = BannerEvent
        fields = ["banner_id", "event", "timestamp"]


class BannerEventsRequestSerializer(serializers.Serializer):
    events = BannerEventSerializer(many=True, allow_empty=False, max_length=50)
