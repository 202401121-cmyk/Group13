"""US-15 (DECIDED 1 and 5): how a product name is normalised before it is matched."""

from __future__ import annotations

from app.entities.normalise import EDITION_WORDS, load_vocabulary, parse_name


def test_units_case_spacing_and_punctuation_are_normalised() -> None:
    a = parse_name("  Apple   iPhone 16,  128 GB  ")
    b = parse_name("apple IPHONE-16 (128gb)")
    assert a.base == b.base == "apple iphone 16"
    assert a.attributes == b.attributes == (("storage", "128GB"),)
    assert parse_name("Anker PowerCore 10000 mAh").numbers == ("10000mah",)
    assert parse_name("Galaxy Watch 44 MM").numbers == ("44mm",)
    assert parse_name("Sony WH-1000XM5").codes == ("wh1000xm5",)  # a hyphenated code is one code


def test_a_repeated_leading_brand_is_dropped() -> None:
    assert parse_name("Sony Sony WH-1000XM5").base == parse_name("Sony WH-1000XM5").base == "sony wh1000xm5"
    assert parse_name("Nova Nova Book 14", brand="Nova").base == "nova 14"
    assert parse_name("Sony WH-1000XM5").brand == "sony"
    assert parse_name("XM5").brand is None


def test_generic_words_from_the_config_are_removed_but_edition_words_stay() -> None:
    vocabulary = load_vocabulary()
    assert {"headphones", "wireless", "best", "book"} <= vocabulary.generic_words
    assert "ultra" in vocabulary.generic_words  # the counting list has it ...
    assert parse_name("1000XM5 wireless headphones").base == "1000xm5"
    s24_ultra = parse_name("Samsung Galaxy S24 Ultra")
    assert "ultra" in s24_ultra.editions and "ultra" in s24_ultra.base  # ... but it names an edition, so it stays
    assert {"pro", "max", "plus", "mini", "lite", "air", "ultra"} <= EDITION_WORDS
    assert parse_name("thin-and-light laptops").base == ""  # every word is generic: nothing names a product
    assert parse_name("thin-and-light laptops").is_vague


def test_codes_numbers_and_variant_attributes_are_found() -> None:
    laptop = parse_name("ASUS ROG Zephyrus G14 16GB RAM 1TB SSD Eclipse Grey India")
    assert laptop.brand == "asus"
    assert laptop.codes == ("g14",)
    assert dict(laptop.attributes) == {"ram": "16GB", "storage": "1TB", "colour": "grey", "region": "IN"}
    assert "eclipse" in laptop.words
    phone = parse_name("Galaxy A35 8GB 256GB Black")
    assert phone.codes == ("a35",)
    assert dict(phone.attributes) == {"ram": "8GB", "storage": "256GB", "colour": "black"}
    assert parse_name("Nova Book 14", brand="Nova").numbers == ("14",)
    assert parse_name("XM5").codes == ("xm5",)
    assert parse_name("Dell").is_vague and parse_name("gaming laptop").is_vague
    assert not parse_name("XM5").is_vague


def test_the_alias_key_is_the_base_without_variants_and_at_most_80_characters() -> None:
    assert parse_name("iPhone 16 256GB Blue").key == parse_name("iPhone 16 128 GB").key == "iphone 16"
    assert len(parse_name("Nova " + "Ultrabook " * 30 + "X1").key) <= 80


def test_plus_ram_plus_storage_joined_numbers_and_sub_brands_are_read() -> None:
    """Reviewer cases: "+" is the Plus edition, "8+128GB" is RAM and storage, "iPhone15" is "iPhone 15"."""
    assert parse_name("Galaxy S24+").base == parse_name("Galaxy S24 Plus").base == "galaxy s24 plus"
    assert "plus" in parse_name("Redmi Note 13 Pro+").editions
    phone = parse_name("Redmi Note 13 8+128GB")
    assert dict(phone.attributes) == {"ram": "8GB", "storage": "128GB"}
    assert phone.numbers == ("13",)
    assert parse_name("iPhone15").base == "iphone 15"
    assert parse_name("Galaxy Buds2 Pro").base == "galaxy buds 2 pro"
    assert parse_name("Sony WH-1000XM5").codes == ("wh1000xm5",)  # a short prefix and digits are a code, not split
    assert parse_name("Xiaomi Redmi Note 13").brands == {"xiaomi", "redmi"}
