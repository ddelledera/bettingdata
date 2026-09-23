name: Test OddsPapi

on:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install requests
      - name: Esegui test
        env:
          ODDSPAPI_KEY: ${{ secrets.ODDSPAPI_KEY }}
        run: python test_oddspapi.py
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: risposte-oddspapi
          path: "*.json"
