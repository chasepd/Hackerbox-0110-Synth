import time, random, math
import board
import busio, displayio, terminalio
import rotaryio, keypad
import audiobusio, audiocore, audiomixer, synthio
import gc9a01
import vectorio
import rainbowio

# right rotary encoder
right_encoder = rotaryio.IncrementalEncoder(board.GP15, board.GP14)
right_key = keypad.Keys( (board.GP7,), value_when_pressed=False)

# left rotary encoder
left_encoder = rotaryio.IncrementalEncoder(board.GP16, board.GP17)
left_key = keypad.Keys( (board.GP18,), value_when_pressed=False)

dw,dh = 240,240
displayio.release_displays()
spi = busio.SPI(clock=board.GP10, MOSI=board.GP11)
display_bus = displayio.FourWire(spi, command=board.GP8, chip_select=board.GP9, reset=board.GP12, baudrate=32_000_000)
display = gc9a01.GC9A01(display_bus, width=dw, height=dh, auto_refresh=False)
maingroup = displayio.Group()
display.root_group = maingroup

# --- Audio Setup ---
i2s_bclk, i2s_lclk, i2s_data = board.GP3, board.GP4, board.GP5
audio = audiobusio.I2SOut(bit_clock=i2s_bclk, word_select=i2s_lclk, data=i2s_data)
mixer = audiomixer.Mixer(voice_count=1, channel_count=1, sample_rate=44100, buffer_size=2048)
audio.play(mixer)
synth = synthio.Synthesizer(channel_count=1, sample_rate=44100)
mixer.voice[0].play(synth)

# Short envelope for percussive sound
def make_env():
    return synthio.Envelope(attack_time=0.01, attack_level=1, sustain_level=0.7, release_time=0.1)

def play_bounce_sound():
    note = synthio.Note(440, envelope=make_env())
    synth.press(note)
    time.sleep(0.05)
    synth.release(note)

def play_reset_sound():
    note = synthio.Note(220, envelope=make_env())
    synth.press(note)
    time.sleep(0.05)
    synth.release(note)

scale_map_mixolydian = (
    0,  # 0
    0,  # 1
    2,  # 2
    2,  # 3
    4,  # 4
    5,  # 5
    7,  # 6
    7,  # 7
    9,  # 8
    10, # 9
    10, # 10
    12, # 11
)

num_colors = 64
bal_pal = displayio.Palette(num_colors)
bal_pal.make_transparent(0)
for i in range(1,num_colors):
    bal_pal[i] = rainbowio.colorwheel( int(i * 255/num_colors) )

# --- Palette for Ball and Paddles ---
pal = displayio.Palette(4)
pal[0] = 0x000000  # transparent/black
pal[1] = 0xFFFFFF  # white (ball)
pal[2] = 0xFF0000  # red (paddle 1)
pal[3] = 0x00FF00  # green (paddle 2)

# --- Game Classes ---
class Ball:
    def __init__(self):
        self.obj = vectorio.Circle(pixel_shader=pal, radius=8, x=dw//2, y=dh//2, color_index=1)
        self.reset()
        self.reset_cooldown = 0

    def reset(self):
        self.x = dw // 2
        self.y = dh // 2
        angle = random.uniform(0, 2 * math.pi)
        speed = 2.5
        self.vx = speed * math.cos(angle)
        self.vy = speed * math.sin(angle)
        self.active = True
        self.bounce_cooldown = 0
        self.reset_cooldown = 0
        self.update_xy()

    def update(self):
        if not self.active:
            return
        self.x += self.vx
        self.y += self.vy
        self.update_xy()
        if self.bounce_cooldown > 0:
            self.bounce_cooldown -= 1
        if self.reset_cooldown > 0:
            self.reset_cooldown -= 1

    def update_xy(self):
        self.obj.x = int(self.x)
        self.obj.y = int(self.y)

    def get_angle_and_radius(self):
        dx = self.x - dw // 2
        dy = self.y - dh // 2
        angle = math.atan2(dy, dx)
        radius = math.sqrt(dx * dx + dy * dy)
        return angle, radius

    def bounce(self, paddle=None):
        # Realistic bounce: reflect velocity about the normal to the edge (from center to ball)
        dx = self.x - dw // 2
        dy = self.y - dh // 2
        norm = math.sqrt(dx * dx + dy * dy)
        if norm == 0:
            return
        nx = dx / norm
        ny = dy / norm
        # Reflect velocity about the normal
        dot = self.vx * nx + self.vy * ny
        self.vx = self.vx - 2 * dot * nx
        self.vy = self.vy - 2 * dot * ny
        # --- Add paddle "spin" (english) ---
        if paddle is not None:
            # Paddle movement direction: positive for CCW, negative for CW
            # Tangential vector is perpendicular to normal
            tx = -ny
            ty = nx
            # Use paddle's last movement delta (in radians)
            spin = getattr(paddle, 'last_spin', 0)
            self.vx += 1.2 * spin * tx
            self.vy += 1.2 * spin * ty
        # --- Add random jitter ---
        angle, _ = self.get_angle_and_radius()
        speed = math.sqrt(self.vx**2 + self.vy**2)
        jitter = random.uniform(-0.15, 0.15)  # radians
        new_angle = math.atan2(self.vy, self.vx) + jitter
        self.vx = speed * math.cos(new_angle)
        self.vy = speed * math.sin(new_angle)
        # Move ball just inside the edge
        self.x = dw // 2 + (100 - 8) * nx
        self.y = dh // 2 + (100 - 8) * ny
        self.update_xy()
        self.bounce_cooldown = 10
        self.reset_cooldown = 20
        play_bounce_sound()

    def is_coordinate_in_ball(self, x, y):
        return (x - self.obj.x)**2 + (y - self.obj.y)**2 <= 8**2

class Paddle:
    def __init__(self, color_index, encoder, initial_angle):
        self.color_index = color_index
        self.encoder = encoder
        self.last_pos = encoder.position
        self.angle = initial_angle  # in radians
        self.width = math.radians(30)  # paddle width in radians
        self.arc_radius = 100 + 6  # radius of the arc (edge of play area)
        self.segments = 16  # number of arc segments
        self.segment_pixel_radius = 1  # single pixel width
        self.group = displayio.Group()
        # Create segment objects
        self.circles = []
        for _ in range(self.segments):
            circ = vectorio.Circle(pixel_shader=pal, radius=self.segment_pixel_radius, x=0, y=0, color_index=color_index)
            self.group.append(circ)
            self.circles.append(circ)
        self.last_spin = 0
        self.update_xy()

    def update(self):
        delta = self.encoder.position - self.last_pos
        if delta != 0:
            self.angle = (self.angle + delta * math.radians(3)) % (2*math.pi)
            self.last_spin = delta * math.radians(3)
            self.last_pos = self.encoder.position
            self.update_xy()
        else:
            self.last_spin = 0

    def update_xy(self):
        # Place arc segments along the edge at the paddle's angle
        mid_angle = self.angle
        start_angle = mid_angle - self.width/2
        for i in range(self.segments):
            frac = i / (self.segments - 1) if self.segments > 1 else 0.5
            seg_angle = start_angle + frac * self.width
            x = int(dw//2 + self.arc_radius * math.cos(seg_angle))
            y = int(dh//2 + self.arc_radius * math.sin(seg_angle))
            self.circles[i].x = x
            self.circles[i].y = y
        self._mid_angle = mid_angle  # for collision

    def collides(self, ball):
        for i in range(self.segments):
            if ball.is_coordinate_in_ball(self.circles[i].x, self.circles[i].y):
                return True
        return False

# --- Game Setup ---
ball = Ball()
paddle1 = Paddle(2, right_encoder, math.radians(0))
paddle2 = Paddle(3, left_encoder, math.radians(180))

maingroup.append(ball.obj)
maingroup.append(paddle1.group)
maingroup.append(paddle2.group)

# --- Main Game Loop ---
last_time = time.monotonic()
while True:
    # Update paddles
    paddle1.update()
    paddle2.update()

    # Update ball
    if ball.active:
        ball.update()
        angle, radius = ball.get_angle_and_radius()
        if radius >= 100:
            colliding1 = paddle1.collides(ball)
            colliding2 = paddle2.collides(ball)
            if colliding1 and ball.bounce_cooldown == 0:
                ball.bounce(paddle1)
            elif colliding2 and ball.bounce_cooldown == 0:
                ball.bounce(paddle2)
            elif not (colliding1 or colliding2) and ball.reset_cooldown == 0 and radius > 120:
                ball.reset()
    else:
        # Ball is inactive, reset after a short pause
        time.sleep(0.5)
        play_reset_sound()
        ball.reset()

    # Refresh display
    display.refresh(target_frames_per_second=30)
    # Small delay for CPU friendliness
    time.sleep(0.01)

