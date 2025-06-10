import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from config import DATA_SOURCE_PATH, DATABASE_URI, TABLE_NAME
from utils import clean_data, validate_data

def ingest_data():
    """Ingest data from a CSV file specified in the configuration."""
    try:
        data = pd.read_csv(DATA_SOURCE_PATH)
        return data
    except Exception as e:
        raise ValueError(f"Error ingesting data: {str(e)}")

def transform_data(data):
    """Transform data using utility functions and basic preprocessing."""
    try:
        # Validate data integrity
        validate_data(data)
        
        # Clean and preprocess data
        data = clean_data(data)
        
        # Additional transformation steps
        data['timestamp'] = pd.to_datetime(data['timestamp'])
        data = data.dropna()
        
        return data
    except Exception as e:
        raise ValueError(f"Error transforming data: {str(e)}")

def store_data(data):
    """Store processed data in a SQL database using the configured URI."""
    try:
        engine = create_engine(DATABASE_URI)
        data.to_sql(TABLE_NAME, engine, if_exists='replace', index=False)
        return True
    except Exception as e:
        raise ValueError(f"Error storing data: {str(e)}")