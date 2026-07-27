extends Node2D
## Phase-3-Client: transparentes Overlay, pollt den Daemon, zeigt den Agent-Status.
## Szene: Node2D (dieses Skript) -> AnimatedSprite2D "Sprite", HTTPRequest "Http",
##        Timer "Poll" (0.25s, autostart), Label "Bubble" (anfangs unsichtbar).

const DAEMON := "http://127.0.0.1:8787"
const PET_SIZE := Vector2i(180, 180)
const MARGIN := 24

# Mood -> Animationsname. Fehlt eine Animation, faellt _play() auf "idle" zurueck.
const MOOD_ANIM := {
	"sleeping":    "sleeping",
	"idle":        "idle",
	"observing":   "observing",
	"thinking":    "thinking",
	"working":     "working",
	"done":        "happy",
	"failed":      "worried",
	"needs_input": "alert",
}

@onready var sprite: AnimatedSprite2D = $Sprite
@onready var http: HTTPRequest = $Http
@onready var bubble: Label = $Bubble

var _last_rev := -1
var _mood := "sleeping"
var _dragging := false
var _grab := Vector2i.ZERO
var _busy := false


func _ready() -> void:
	_setup_window()
	bubble.visible = false
	$Poll.timeout.connect(_poll)
	http.request_completed.connect(_on_state)
	_play("idle")


func _setup_window() -> void:
	get_viewport().transparent_bg = true
	var ds := DisplayServer
	ds.window_set_flag(ds.WINDOW_FLAG_TRANSPARENT, true)
	ds.window_set_flag(ds.WINDOW_FLAG_BORDERLESS, true)
	ds.window_set_flag(ds.WINDOW_FLAG_ALWAYS_ON_TOP, true)
	ds.window_set_size(PET_SIZE)

	# Ruhig im Leerlauf. Waehrend Drag/Animation gehen wir kurz hoch.
	OS.low_processor_usage_mode = true
	Engine.max_fps = 12

	var usable := ds.screen_get_usable_rect()
	ds.window_set_position(Vector2i(
		usable.end.x - PET_SIZE.x - MARGIN,
		usable.end.y - PET_SIZE.y - MARGIN
	))


# ---------------------------------------------------------------- Daemon

func _poll() -> void:
	if _busy:
		return
	_busy = true
	http.request(DAEMON + "/state")


func _on_state(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_busy = false
	if code != 200:
		_set_mood("sleeping")   # Daemon weg -> Pet schlaeft, statt zu luegen
		return

	var data: Variant = JSON.parse_string(body.get_string_from_utf8())
	if typeof(data) != TYPE_DICTIONARY:
		return
	if data.get("rev", -1) == _last_rev:
		return
	_last_rev = data["rev"]

	_set_mood(str(data.get("mood", "sleeping")))

	var b: Variant = data.get("bubble")
	if b is Dictionary and _mood in ["done", "failed", "needs_input"]:
		_show_bubble(b)
	else:
		bubble.visible = false


# ---------------------------------------------------------------- Darstellung

func _set_mood(m: String) -> void:
	if m == _mood:
		return
	_mood = m
	_play(MOOD_ANIM.get(m, "idle"))

	# Eskalationsstufe 5 (Ton) nur bei needs_input. Alles andere bleibt still.
	if m == "needs_input" and has_node("Chime"):
		$Chime.play()


func _play(anim: String) -> void:
	var frames := sprite.sprite_frames
	if frames == null:
		return
	if not frames.has_animation(anim):
		anim = "idle"
	if sprite.animation != anim:
		sprite.play(anim)


func _show_bubble(b: Dictionary) -> void:
	bubble.text = "%s\n%s" % [b.get("title", ""), b.get("body", "")]
	bubble.visible = true


func _dismiss_bubble() -> void:
	bubble.visible = false
	var req := HTTPRequest.new()
	add_child(req)
	req.request(DAEMON + "/bubble/dismiss", [], HTTPClient.METHOD_POST, "{}")
	req.request_completed.connect(func(_a, _b, _c, _d): req.queue_free())


# ---------------------------------------------------------------- Eingabe

func _unhandled_input(e: InputEvent) -> void:
	if e is InputEventMouseButton and e.button_index == MOUSE_BUTTON_LEFT:
		if e.pressed:
			if bubble.visible:
				_dismiss_bubble()
				return
			_dragging = true
			_grab = Vector2i(e.position)
			Engine.max_fps = 60
		else:
			_dragging = false
			Engine.max_fps = 12
	elif e is InputEventMouseMotion and _dragging:
		var pos := DisplayServer.window_get_position()
		DisplayServer.window_set_position(pos + Vector2i(e.position) - _grab)
