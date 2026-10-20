import tkinter as tk
from tkinter import ttk
import serial
from serial.tools import list_ports
import threading
import time
import datetime

# ========== إعدادات عامة ==========
ser = None
connected = False
current_seq_id = 0x80
entered_speed = ""
stop_heartbeat = threading.Event()

# ========== دوال البروتوكول ==========
def get_next_seq():
    global current_seq_id
    seq = current_seq_id
    current_seq_id = (seq + 1) & 0xFF
    if current_seq_id > 0x8F: current_seq_id = 0x80
    return seq

def stuff_bytes(byte_list):
    stuffed = []
    for b in byte_list:
        stuffed.append(b)
        if b == 0x10: stuffed.append(0x10)
    return stuffed

def build_custom_frame(sequence_id, func_code, payload):
    data = [sequence_id, func_code] + payload
    checksum = sum(data) % 256
    stuffed_data = stuff_bytes(data)
    stuffed_checksum = stuff_bytes([checksum])
    return bytes([0x10, 0x02] + stuffed_data + [0x10, 0x03] + stuffed_checksum)

def send_raw(byte_data):
    global connected, ser
    hex_str = " ".join(f"{b:02X}" for b in byte_data)
    now = datetime.datetime.now().strftime("%H:%M:%S")
    if connected and ser and ser.is_open:
        try:
            ser.write(byte_data)
            print(f"[{now}] TX -> {hex_str}")
        except Exception as e:
            disconnect_driver()
            print(f"TX Error: {e}")
    else:
        print(f"[{now}] OFFLINE SIMULATION -> {hex_str}")

def send_ack():
    send_raw(bytes([0x10, 0x06]))

def initialize_drive():
    def seq():
        time.sleep(0.2)
        send_raw(build_custom_frame(get_next_seq(), 0x0E, [0x00, 0x00, 0x01]))
        time.sleep(0.1)
        send_raw(build_custom_frame(get_next_seq(), 0x22, [0x00, 0x01]))
        time.sleep(0.1)
        send_raw(build_custom_frame(get_next_seq(), 0x16, [0x00]*8)) 
        time.sleep(0.1)
        
        magic_payload = [
            0x00, 0x0F, 0x00, 0x27, 0x00, 0x06, 0x20, 0x29, 0x20, 0x35, 
            0x20, 0x05, 0x30, 0x05, 0x00, 0x00, 0x00, 0x01, 0x00, 0x02, 
            0x00, 0x03, 0x10, 0x28, 0x10, 0x26, 0x10, 0x27, 0x20, 0x06, 
            0x20, 0x00, 0x20, 0x01, 0x20, 0x07
        ]
        send_raw(build_custom_frame(get_next_seq(), 0x16, magic_payload))
        print("Stream Configured! Drive will send Actual Values.")
    threading.Thread(target=seq, daemon=True).start()

def heartbeat_loop():
    global connected
    while not stop_heartbeat.is_set():
        if connected:
            send_ack()
        time.sleep(0.8)

def read_serial_loop():
    global connected, ser
    state = 'WAIT_DLE'
    frame_data = []
    while True:
        if connected and ser and ser.is_open:
            try:
                if ser.in_waiting:
                    b = ser.read(1)[0]
                    if state == 'WAIT_DLE':
                        if b == 0x10: state = 'WAIT_STX'
                    elif state == 'WAIT_STX':
                        if b in [0x02, 0x01]:
                            state = 'IN_MSG'
                            frame_data.clear()
                        else:
                            state = 'WAIT_DLE'
                    elif state == 'IN_MSG':
                        if b == 0x10:
                            state = 'ESCAPE'
                        else:
                            frame_data.append(b)
                    elif state == 'ESCAPE':
                        if b == 0x03:
                            state = 'WAIT_CHECKSUM'
                        elif b == 0x10:
                            frame_data.append(0x10)
                            state = 'IN_MSG'
                        elif b in [0x02, 0x01]:
                            frame_data.clear()
                            state = 'IN_MSG'
                        else:
                            state = 'WAIT_DLE'
                    elif state == 'WAIT_CHECKSUM':
                        received_cs = b
                        if frame_data:
                            expected_cs = sum(frame_data) % 256
                            if received_cs == expected_cs:
                                process_valid_frame(frame_data)
                        state = 'WAIT_DLE'
                else:
                    time.sleep(0.01)
            except Exception as e:
                print(f"Read error: {e}")
                disconnect_driver()
        else:
            time.sleep(0.1)

def process_valid_frame(data):
    send_ack()
    
    if len(data) >= 4:
        func = data[1]
        
        if func == 0x1C and len(data) >= 34:
            # التعديل هنا: قراءة السرعة من البايت 8 و 9 بناءً على تحليل الـ Sniffing
            freq_raw = (data[8] << 8) | data[9]
            if freq_raw > 32767: 
                freq_raw -= 65536
            freq_hz = freq_raw / 100.0
            
            # التعديل هنا: قراءة التيار من البايت 14 و 15
            current_raw = (data[14] << 8) | data[15]
            current_amps = current_raw / 10.0
            
            root.after(0, lambda: actual_speed_var.set(f"{freq_hz:.2f} Hz"))
            root.after(0, lambda: actual_current_var.set(f"{current_amps:.1f} A"))

# ========== دوال الأوامر ==========
def action_start():
    send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x34, 0x00, 0x01]))

def action_stop():
    send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x34, 0x00, 0x00]))

def action_fwd():
    send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x36, 0x00, 0x00]))

def action_rev():
    send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x36, 0x00, 0x01]))

def action_fault_reset():
    def seq():
        send_raw(build_custom_frame(get_next_seq(), 0x0C, [0x00, 0x00, 0x00, 0x01]))
        time.sleep(0.1)
        send_raw(build_custom_frame(get_next_seq(), 0x0C, [0x00, 0x00, 0x00, 0x00]))
        time.sleep(0.1)
        send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x34, 0x00, 0x00]))
    threading.Thread(target=seq, daemon=True).start()

def action_reset():
    def seq():
        send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x35, 0x00, 0x01]))
        time.sleep(0.1)
        send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x35, 0x00, 0x00]))
    threading.Thread(target=seq, daemon=True).start()

def action_set_speed(freq):
    def seq():
        val = int(round(freq * 100))
        vh = (val >> 8) & 0xFF
        vl = val & 0xFF
        send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x37, vh, vl]))
        time.sleep(0.05)
        send_raw(build_custom_frame(get_next_seq(), 0x0D, [0x00, 0x33, 0x00, 0x01]))
    threading.Thread(target=seq, daemon=True).start()

def refresh_ports():
    ports = [p.device for p in list_ports.comports()]
    port_combo['values'] = ports
    if ports: port_combo.current(0)

def connect_driver():
    global ser, connected, stop_heartbeat
    port = port_combo.get()
    if not port: return
    try:
        ser = serial.Serial(port, baudrate=9600, timeout=1)
        connected = True
        stop_heartbeat.clear()
        threading.Thread(target=heartbeat_loop, daemon=True).start()
        initialize_drive()
        status_led.itemconfig(led_circle, fill="#2ecc71")
        btn_connect.config(text="DISCONNECT", bg="#e74c3c", command=disconnect_driver)
    except Exception as e:
        print(f"Connection failed: {e}")

def disconnect_driver():
    global ser, connected, stop_heartbeat
    connected = False
    stop_heartbeat.set()
    
    if ser and ser.is_open:
        ser.close()
        
    status_led.itemconfig(led_circle, fill="#e74c3c")
    btn_connect.config(text="CONNECT", bg="#27ae60", command=connect_driver)
    actual_speed_var.set("0.00 Hz")
    actual_current_var.set("0.0 A")

def press_number(num):
    global entered_speed
    entered_speed += str(num)
    target_speed_var.set(entered_speed)

def clear_last():
    global entered_speed
    entered_speed = entered_speed[:-1]
    target_speed_var.set(entered_speed)

def enter_speed():
    global entered_speed
    if entered_speed:
        try:
            freq = float(entered_speed)
            action_set_speed(freq)
        except ValueError:
            pass
        entered_speed = ""
        target_speed_var.set("")

root = tk.Tk()
root.title("Vacon HMI Control")
root.geometry("480x320")
root.resizable(False, False)
root.configure(bg="#2c3e50")

actual_speed_var = tk.StringVar(value="0.00 Hz")
actual_current_var = tk.StringVar(value="0.0 A")
target_speed_var = tk.StringVar(value="")

top_frame = tk.Frame(root, bg="#34495e", height=40)
top_frame.pack(fill="x")

port_combo = ttk.Combobox(top_frame, width=15, state="readonly", font=("Arial",10))
port_combo.pack(side="left", padx=10, pady=5)

tk.Button(top_frame, text="↻", font=("Arial",10,"bold"), bg="#f39c12", fg="white", command=refresh_ports).pack(side="left", padx=5)

btn_connect = tk.Button(top_frame, text="CONNECT", font=("Arial",10,"bold"), bg="#27ae60", fg="white", width=12, command=connect_driver)
btn_connect.pack(side="left", padx=10, pady=5)

status_led = tk.Canvas(top_frame, width=20, height=20, bg="#34495e", highlightthickness=0)
led_circle = status_led.create_oval(2,2,18,18, fill="#e74c3c", outline="white")
status_led.pack(side="right", padx=15)

bottom = tk.Frame(root, bg="#2c3e50")
bottom.pack(fill="both", expand=True)

left = tk.Frame(bottom, bg="#2c3e50", width=180)
left.pack(side="left", fill="y", padx=5, pady=5)
left.pack_propagate(False)

tk.Label(left, text="SET SPEED", font=("Arial",10,"bold"), bg="#2c3e50", fg="white").pack()
tk.Entry(left, textvariable=target_speed_var, font=("Arial",18,"bold"), justify="center", state="readonly", width=8).pack(pady=2)

keys = tk.Frame(left, bg="#2c3e50")
keys.pack(expand=True)

btns = [
    ("7",0,0),("8",0,1),("9",0,2),
    ("4",1,0),("5",1,1),("6",1,2),
    ("1",2,0),("2",2,1),("3",2,2),
    (".",3,0),("0",3,1),("DEL",3,2),
]
for (t,r,c) in btns:
    if t.isdigit() or t=='.':
        act = lambda v=t: press_number(v); col = "#2980b9"
    else:
        act = clear_last; col = "#e67e22"
    tk.Button(keys, text=t, command=act, font=("Arial",12,"bold"), bg=col, fg="white", width=4).grid(row=r, column=c, padx=2, pady=2)
tk.Button(keys, text="ENTER", command=enter_speed, font=("Arial",10,"bold"), bg="#16a085", fg="white").grid(row=4, column=0, columnspan=3, sticky="we", padx=2, pady=2)

right = tk.Frame(bottom, bg="#2c3e50")
right.pack(side="right", fill="both", expand=True, padx=5, pady=5)

display = tk.Frame(right, bg="#34495e", bd=2, relief="sunken")
display.pack(fill="x", pady=5)
display.columnconfigure(0, weight=1)
display.columnconfigure(1, weight=1)

tk.Label(display, text="SPEED", font=("Arial",10,"bold"), bg="#34495e", fg="#bdc3c7").grid(row=0, column=0, pady=(5,0))
tk.Label(display, textvariable=actual_speed_var, font=("Arial",18,"bold"), bg="#34495e", fg="#2ecc71").grid(row=1, column=0, pady=(0,5))

tk.Label(display, text="CURRENT", font=("Arial",10,"bold"), bg="#34495e", fg="#bdc3c7").grid(row=0, column=1, pady=(5,0))
tk.Label(display, textvariable=actual_current_var, font=("Arial",18,"bold"), bg="#34495e", fg="#f1c40f").grid(row=1, column=1, pady=(0,5))

btns_frame = tk.Frame(right, bg="#2c3e50")
btns_frame.pack(expand=True, fill="both")

tk.Button(btns_frame, text="START", font=("Arial",10,"bold"), bg="#27ae60", fg="white", command=action_start, height=2, width=10).grid(row=0, column=0, padx=3, pady=3)
tk.Button(btns_frame, text="STOP", font=("Arial",10,"bold"), bg="#c0392b", fg="white", command=action_stop, height=2, width=10).grid(row=0, column=1, padx=3, pady=3)
tk.Button(btns_frame, text="FWD ⟳", font=("Arial",10,"bold"), bg="#8e44ad", fg="white", command=action_fwd, height=2, width=10).grid(row=1, column=0, padx=3, pady=3)
tk.Button(btns_frame, text="REV ⟲", font=("Arial",10,"bold"), bg="#2980b9", fg="white", command=action_rev, height=2, width=10).grid(row=1, column=1, padx=3, pady=3)
tk.Button(btns_frame, text="FAULT RST", font=("Arial",10,"bold"), bg="#e67e22", fg="white", command=action_fault_reset, height=2, width=10).grid(row=2, column=0, padx=3, pady=3)
tk.Button(btns_frame, text="RESET", font=("Arial",10,"bold"), bg="#7f8c8d", fg="white", command=action_reset, height=2, width=10).grid(row=2, column=1, padx=3, pady=3)


refresh_ports()
threading.Thread(target=read_serial_loop, daemon=True).start()
root.mainloop()
