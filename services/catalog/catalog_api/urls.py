from django.urls import path

from .views import (
    BreadcrumbsView,
    CatalogFacetsView,
    CategoryDetailView,
    CategoryFiltersView,
    CategoryTreeView,
    ProductDetailView,
    ProductListView,
    ProductSimilarView,
    ProductSkuDetailView,
    ProductSkuListView,
)


urlpatterns = [
    path("products", ProductListView.as_view(), name="products-list-no-slash"),
    path("products/", ProductListView.as_view(), name="products-list"),
    path("products/<uuid:id>", ProductDetailView.as_view(), name="products-detail-no-slash"),
    path("products/<uuid:id>/", ProductDetailView.as_view(), name="products-detail"),
    path("products/<uuid:id>/similar", ProductSimilarView.as_view(), name="products-similar-no-slash"),
    path("products/<uuid:id>/similar/", ProductSimilarView.as_view(), name="products-similar"),
    path("products/<uuid:product_id>/skus", ProductSkuListView.as_view(), name="products-skus-no-slash"),
    path("products/<uuid:product_id>/skus/", ProductSkuListView.as_view(), name="products-skus"),
    path(
        "products/<uuid:product_id>/skus/<uuid:sku_id>",
        ProductSkuDetailView.as_view(),
        name="products-sku-detail-no-slash",
    ),
    path(
        "products/<uuid:product_id>/skus/<uuid:sku_id>/",
        ProductSkuDetailView.as_view(),
        name="products-sku-detail",
    ),
    path("categories", CategoryTreeView.as_view(), name="categories-list-no-slash"),
    path("categories/", CategoryTreeView.as_view(), name="categories-list"),
    path("categories/tree", CategoryTreeView.as_view(), name="categories-tree-no-slash"),
    path("categories/tree/", CategoryTreeView.as_view(), name="categories-tree"),
    path("categories/<uuid:id>", CategoryDetailView.as_view(), name="categories-detail-no-slash"),
    path("categories/<uuid:id>/", CategoryDetailView.as_view(), name="categories-detail"),
    path("categories/<uuid:id>/filters", CategoryFiltersView.as_view(), name="categories-filters-no-slash"),
    path("categories/<uuid:id>/filters/", CategoryFiltersView.as_view(), name="categories-filters"),
    path("catalog/facets", CatalogFacetsView.as_view(), name="catalog-facets-no-slash"),
    path("catalog/facets/", CatalogFacetsView.as_view(), name="catalog-facets"),
    path("breadcrumbs", BreadcrumbsView.as_view(), name="breadcrumbs-no-slash"),
    path("breadcrumbs/", BreadcrumbsView.as_view(), name="breadcrumbs"),
]
