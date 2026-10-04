from codesense.database import SessionLocal, User
from codesense.auth import verify_password, get_password_hash

db = SessionLocal()
try:
    user = db.query(User).filter(User.email == "test@test.com").first()
    if not user:
        print("User not found, simulating registration")
        user = User(email="test@test.com", hashed_password=get_password_hash("superlongpassword"*10))
        db.add(user)
        db.commit()
    else:
        print("Verifying password...")
        print(verify_password("superlongpassword"*10, user.hashed_password))
except Exception as e:
    import traceback
    traceback.print_exc()
