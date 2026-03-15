import win32com.client
import pythoncom
import threading

class SapiSpeaker:
    def __init__(self):
        self.lock = threading.Lock()
        self.voice = None

    def _ensure_voice(self):
        if self.voice is None:
            pythoncom.CoInitialize()
            self.voice = win32com.client.Dispatch("SAPI.SpVoice")
            # 可选设置中文语音
            for v in self.voice.GetVoices():
                if 'Chinese' in v.GetDescription():
                    self.voice.Voice = v
                    break

    def speak(self, text):
        with self.lock:
            self._ensure_voice()
            self.voice.Speak(text)

# 使用
speaker = SapiSpeaker()
speaker.speak("你好，这是第一次")
speaker.speak("第二次也正常")