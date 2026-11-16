"""US-24: a listing is this exact model only when its title names the brand and model (DECIDED 2). Never fuzzy."""

from __future__ import annotations

import pytest

from app.commerce.matching import accepts, query_for, store_search_url, variant_for
from app.contracts import Product, Variant

VIVOBOOK = Product(id="prd_asus_vivobook_15_x1504va", name="ASUS Vivobook 15 X1504VA", brand="ASUS")
IPHONE = Product(id="prd_apple_iphone_15", name="iPhone 15", brand="Apple")
VICTUS = Product(id="prd_hp_victus_15", name="HP Victus 15", brand="HP")
NOVA = Product(id="prd_nova_book_14", name="Nova Book 14", brand="Nova")
TWO_VARIANTS = Product(
    id="prd_lenovo_ideapad_slim_3",
    name="Lenovo IdeaPad Slim 3 15IAH8",
    brand="Lenovo",
    variants=[
        Variant(id="var_ideapad_16_512", label="16 GB RAM / 512 GB", attributes={"ram": "16GB", "storage": "512GB"}),
        Variant(id="var_ideapad_8_512", label="8 GB RAM / 512 GB", attributes={"ram": "8GB", "storage": "512GB"}),
    ],
)


@pytest.mark.parametrize(
    ("product", "title"),
    [
        (
            VIVOBOOK,
            'ASUS Vivobook 15, Intel Core i5-1335U 13th Gen (16GB RAM/512GB SSD/15.6" FHD) X1504VA-NJ542WS Laptop',
        ),
        (VIVOBOOK, "Asus VivoBook 15 X1504VA Laptop, Cool Silver"),
        (IPHONE, "Apple iPhone 15 (128 GB) - Black"),
        (VICTUS, "HP Victus 15 Gaming Laptop, 16GB RAM, Windows 11 Pro"),  # "Pro" after Windows 11, not the model
        (NOVA, "Nova Book 14 (16GB RAM, 512GB SSD) Laptop"),  # a brand outside the brands list
    ],
)
def test_the_exact_model_is_accepted(product: Product, title: str) -> None:
    assert accepts(product, title)


@pytest.mark.parametrize(
    ("product", "title", "why"),
    [
        (VIVOBOOK, "ASUS Vivobook 15 X1502ZA Intel Core i5 Laptop", "a different model code"),
        (VIVOBOOK, "ASUS Vivobook 15 Laptop (16GB/512GB)", "no model code to agree with"),
        (VIVOBOOK, "Acer Aspire 15 X1504VA Laptop", "another brand"),
        (VIVOBOOK, "ASUS Vivobook 16 X1504VA Laptop", "another number"),
        (IPHONE, "Apple iPhone 15 Pro (256 GB) - Natural Titanium", "an edition the product doesn't have"),
        (IPHONE, "Apple iPhone 15 Plus (128 GB)", "an edition the product doesn't have"),
        (IPHONE, "Apple iPhone 16 (128 GB)", "another number"),
        (VICTUS, "HP Omen 15 Gaming Laptop", "a missing model word"),
        (VICTUS, "Laptop for gaming, 15 inch", "no brand and no model"),
    ],
)
def test_anything_else_is_refused(product: Product, title: str, why: str) -> None:
    assert not accepts(product, title), why


def test_a_product_without_a_brand_needs_a_model_code() -> None:
    nameless = Product(id="prd_book_14", name="Book 14")
    coded = Product(id="prd_x1504va", name="Vivobook X1504VA")
    assert not accepts(nameless, "Book 14 Laptop")
    assert accepts(coded, "ASUS Vivobook X1504VA Laptop")


def test_a_vague_product_is_never_priced() -> None:
    assert not accepts(Product(id="prd_asus", name="ASUS", brand="ASUS"), "ASUS Vivobook 15 Laptop")


def test_a_stated_matching_variant_is_set() -> None:
    title = "Lenovo IdeaPad Slim 3 15IAH8 Intel Core i5 (16GB RAM/512GB SSD) Laptop"
    variant = variant_for(TWO_VARIANTS, title)
    assert variant is not None and variant.id == "var_ideapad_16_512"


def test_a_variant_mismatch_sets_no_variant() -> None:
    title = "Lenovo IdeaPad Slim 3 15IAH8 (12GB RAM/1TB SSD) Laptop"
    assert accepts(TWO_VARIANTS, title)  # still the model
    assert variant_for(TWO_VARIANTS, title) is None  # but neither of its variants


def test_an_unstated_variant_is_none() -> None:
    assert variant_for(TWO_VARIANTS, "Lenovo IdeaPad Slim 3 15IAH8 Laptop") is None
    assert variant_for(TWO_VARIANTS, "Lenovo IdeaPad Slim 3 15IAH8 (512GB SSD) Laptop") is None  # half stated


def test_the_query_and_the_store_search() -> None:
    assert query_for(VIVOBOOK) == "ASUS Vivobook 15 X1504VA"  # the brand is already in the name
    assert query_for(IPHONE) == "Apple iPhone 15"
    assert store_search_url(IPHONE) == "https://www.google.com/search?tbm=shop&q=Apple+iPhone+15"


ZENBOOK = Product(id="prd_asus_zenbook_14_2025", name="ASUS ZenBook 14 (2025)", brand="ASUS")
OMNIBOOK = Product(id="prd_hp_omnibook_3_14_2025", name="HP OmniBook 3 14 (2025)", brand="HP")


def test_the_models_words_must_be_in_order_with_nothing_between() -> None:
    assert accepts(ZENBOOK, "ASUS Zenbook 14 OLED Core Ultra 5 225H")
    assert not accepts(ZENBOOK, "Asus Zenbook S 14")  # another series
    assert accepts(OMNIBOOK, 'HP OmniBook 3 Laptop 14 " -35.6 cm diagonal 2K')  # "Laptop" is not part of a name
    assert not accepts(OMNIBOOK, "HP 14 OmniBook 3 Next Gen AI Laptop")  # in another order: not taken on trust


def test_a_year_in_the_products_name_is_not_needed_but_another_year_is_another_model() -> None:
    assert accepts(ZENBOOK, "Asus Zenbook 14 UX3405CA-QL1691WS Thin & Light")
    assert accepts(ZENBOOK, "ASUS Zenbook 14 (2025) Intel Core Ultra 7")
    assert not accepts(ZENBOOK, "ASUS Zenbook 14 (2024) Intel Core Ultra 7")
    assert not accepts(Product(id="prd_apple_ipad_2022", name="iPad (2022)", brand="Apple"), "Apple iPad 2021 Wi-Fi")


def test_a_gpu_number_is_never_taken_for_a_year() -> None:
    msi = Product(id="prd_msi_gf63_rtx_2050", name="MSI GF63 Thin RTX 2050", brand="MSI")
    assert not accepts(msi, "MSI GF63 Thin RTX 3050 Gaming Laptop")


@pytest.mark.parametrize(
    ("product", "title"),
    [
        (IPHONE, "Spigen Ultra Hybrid Case for Apple iPhone 15"),
        (VIVOBOOK, "65W Charger for ASUS Vivobook 15 X1504VA Laptop"),
        (IPHONE, "Apple iPhone 15 Silicone Case with MagSafe"),
        (IPHONE, "Apple iPhone 15 (Renewed) 128 GB"),
        (IPHONE, "Apple iPhone 15 128GB Pre-Owned"),
    ],
)
def test_an_accessory_or_a_second_hand_listing_is_refused(product: Product, title: str) -> None:
    assert not accepts(product, title)


def test_an_accessory_that_comes_with_it_is_still_the_product() -> None:
    buds = Product(id="prd_boat_airdopes_141", name="boAt Airdopes 141", brand="boAt")
    assert accepts(buds, "boAt Airdopes 141 TWS Earbuds with 42H Playtime and Charging Case")
    assert accepts(VIVOBOOK, "ASUS Vivobook 15 X1504VA (16GB/512GB) Laptop with Charger")


@pytest.mark.parametrize(
    ("product", "title"),
    [
        (Product(id="prd_lenovo_ideapad_3", name="IdeaPad 3", brand="Lenovo"), "Lenovo IdeaPad Gaming 3 Laptop"),
        (Product(id="prd_hp_pavilion_14", name="Pavilion 14", brand="HP"), "HP Pavilion x360 14 2-in-1 Laptop"),
        (Product(id="prd_hp_15", name="15", brand="HP"), "HP Victus 15 Gaming Laptop"),
    ],
)
def test_a_series_word_between_the_brand_and_the_model_is_another_product(product: Product, title: str) -> None:
    assert not accepts(product, title)


def test_a_brand_of_two_words_and_a_sub_brand_are_found() -> None:
    watch = Product(id="prd_fireboltt_ninja_call_pro", name="Ninja Call Pro", brand="Fire-Boltt")
    assert accepts(watch, "Fire-Boltt Ninja Call Pro Smart Watch, 1.83 inch")
    redmi = Product(id="prd_redmi_note_13", name="Redmi Note 13", brand="Xiaomi")
    assert accepts(redmi, "Redmi Note 13 5G (Arctic White, 8GB RAM, 256GB Storage)")
