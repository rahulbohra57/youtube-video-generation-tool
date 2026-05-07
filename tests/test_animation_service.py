from PIL import Image, ImageDraw


def _make_test_image(path: str):
    img = Image.new("RGB", (256, 256), (20, 30, 40))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(20, 20), (120, 120)], fill=(220, 140, 40))
    draw.ellipse([(150, 80), (230, 180)], fill=(40, 190, 220))
    img.save(path)


def test_resolve_motion_hint_defaults_by_genre():
    from app.services.animation_service import resolve_motion_hint

    hint = resolve_motion_hint(scene={}, idx=1, genre="comedy", profile="standard")
    assert hint["motion_type"] == "pop_in"
    assert hint["camera_path"] == "right_to_left"
    assert hint["transition"] in ("dissolve", "push", "flash_cut", "whip")
    assert hint["effect_cue"] == "subtle glow"


def test_create_animated_scene_clip_returns_clip_object(tmp_path):
    from app.services.animation_service import create_animated_scene_clip

    image_path = str(tmp_path / "frame.png")
    _make_test_image(image_path)

    clip = create_animated_scene_clip(
        image_path=image_path,
        duration=2.0,
        motion_hint={
            "motion_type": "ken_burns",
            "camera_path": "left_to_right",
            "transition": "dissolve",
        },
        profile="standard",
    )
    assert clip is not None
