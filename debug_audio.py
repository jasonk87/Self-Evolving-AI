import pyaudio
import sys

def test_audio():
    print("Initializing PyAudio...")
    try:
        p = pyaudio.PyAudio()
        info = p.get_host_api_info_by_index(0)
        numdevices = info.get('deviceCount')
        
        print(f"Found {numdevices} devices.")
        input_devices = []

        for i in range(0, numdevices):
            if (p.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
                dev = p.get_device_info_by_host_api_device_index(0, i)
                print(f"Input Device id {i} - {dev.get('name')}")
                input_devices.append(i)

        if not input_devices:
            print("ERROR: No input (microphone) devices found!")
            return

        print("\nAttempting to open stream on default device...")
        try:
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=24000, input=True, frames_per_buffer=1024)
            print("SUCCESS: Stream opened.")
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"ERROR: Failed to open stream: {e}")

        p.terminate()

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

if __name__ == "__main__":
    test_audio()
