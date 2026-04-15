import streamlit as st
from supabase import create_client
from supabase.client import ClientOptions


@st.cache_resource
def get_admin_client():
    return create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["service_role_key"],
        options=ClientOptions(
            auto_refresh_token=False,
            persist_session=False,
        ),
    )