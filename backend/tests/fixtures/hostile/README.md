# Hostile pages (US-28)

Every file here is synthetic, made up for ShopSense's tests. The products, people and pages do not exist.

Each attack has a `.hostile` file and a `.clean` twin that is the same without the attack. `tests/faults/test_hostile_pages.py` runs both through cleaning, extraction with a model that obeys any order it can see, and the decide stage, and the results must be identical.

| Attack | What it tries |
|---|---|
| 01_visible_text | An order in plain visible text |
| 02_hidden_element | An order in an element hidden from readers |
| 03_html_comment | An order in an HTML comment |
| 04_zero_width | An order in invisible tag characters, and one split up by zero-width characters |
| 05_fake_role_message | A fake system message and a fake assistant reply, with a fake closing marker for the data block |
| 06_fake_json | A fake JSON answer, with an order to return it |
| 07_reddit_comment | An order in a Reddit comment |
| 08_video_description | An order in a video description |
| 09_rank_first | A page asking to rank a named product first |
| 10_drop_product | A page asking to drop a product |
