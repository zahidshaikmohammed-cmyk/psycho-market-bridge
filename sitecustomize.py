import os
import re
import sys

try:
    from flask import Flask, Response

    _original_run = Flask.run

    def _psycho_run(self, *args, **kwargs):
        try:
            script = os.path.basename(sys.argv[0] or "")
            if script == "bridge.py" and os.path.exists("landing.py"):
                with open("landing.py", "r", encoding="utf-8") as file:
                    source = file.read()

                marker = "PSYCHO_HTML = r'''"
                start = source.find(marker)
                if start >= 0:
                    start += len(marker)
                    end = source.find("'''", start)
                    if end >= 0:
                        html = source[start:end]

                        def psycho_home():
                            return Response(html, content_type="text/html; charset=utf-8")

                        self.view_functions["home"] = psycho_home
                        print("PSYCHO LANDING: ROOT ROUTE ENABLED", flush=True)
        except Exception as error:
            print(f"PSYCHO LANDING HOOK ERROR: {error}", flush=True)

        return _original_run(self, *args, **kwargs)

    Flask.run = _psycho_run
except Exception as error:
    print(f"PSYCHO SITE CUSTOMIZE ERROR: {error}", flush=True)
