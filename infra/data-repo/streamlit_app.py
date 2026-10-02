"""Entry point for Streamlit Community Cloud.

The viewer lives in keirin-platform (installed from requirements.txt); this
repository only provides the data. See keirin-platform docs/deploy-streamlit.md.
"""

from keirin.viewer import main

main()
